# Content-Addressed Image Store — Stage 4 (Migration and GC) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** an explicit, restartable maintenance operation that moves every legacy image file, sidecar and format-1 collection into the image store, plus a collector that removes images nothing reaches any more. Both are started from Settings → Storage, run as one-at-a-time detached runs, and leave a report behind.
- **Migration** produces placements, objects with merged metadata, and format-2 manifests. It then verifies and deletes the legacy copies.
- **The collector** removes images that no placement, manifest or journal reaches, after a grace period and two scans.

**Architecture:**
- **A new run class, `maintenance`**, on the `("global",)` subject, with exclusion key `("global", "image-maintenance")`. Its sync work runs in a worker thread and checks a cancel flag between items. Its report is persisted to `.cache/image-store/reports/<run>.json`.
- **Per-name image locks become process-scoped and striped**, so a second process (or this one's maintenance thread) serializes correctly.
- **New store modules:**
  - `image_surfaces`: the roster of where images live, built from the code's own path builders;
  - `image_migration`: inventory, plan, write, verify, clean up;
  - `image_gc`: roots, candidates, two-scan state, deletion.
- **Routes and UI:**
  - `POST /api/maintenance/images/migrate` and `POST /api/maintenance/images/gc`, plus `GET /api/maintenance/images/reports/{run}`;
  - a Settings → Storage "Image store" card.

**Tech Stack:** Python 3.11+, FastAPI, anyio, pytest; React + TypeScript, vitest.

**Spec:** `docs/superpowers/specs/2026-10-05-content-addressed-image-store-design.md`, stage 4 of §14. Read §11 and §12 in full, §13 "Migration" and "GC", and every amendment section (stages 1, 2 and 3). A code map is at `.superpowers/sdd/stage4-notes/code-map.md`.

**Precondition:** stages 2 and 3 are complete on this branch. In particular this plan relies on:
- `image_store.iter_ids`, `read_fresh` and `update -> bool`;
- `image_scopes`;
- `image_usage`;
- format-2 collections and `has_format1`;
- the stage-2 description and subject façades.

## Stage-4 rulings (the spec gains them in Task 8)

**M1. The `maintenance` run class.**
- `RunClass` gains `"maintenance"`.
- `exclusion_key` gives it `"global\x00image-maintenance"`, derived from the class rather than the subject alone, so no other global run is refused by it.
- It is neither notifying nor attachable.
- It is started by `runs.run_maintenance(app, kind, attempt, work_sync)`:
  - it reserves the run (a second start answers 409 `run_in_flight`; a store move answers 409 `busy`);
  - it runs `work_sync(run)` through `anyio.to_thread.run_sync`;
  - `work_sync` checks `run.cancel_requested` between items and stops cleanly, reporting what it did so far.
- `PUT /config/data-dir` is refused while a maintenance run is live, through the existing `store_held_still`.

**M2. Reports.**
- Each run writes `.cache/image-store/reports/<run id>.json` (atomic) and also returns it as its result.
- A dry run and a real run produce the same report shape (§11).
- Reports hold paths inside the store and counts. They are never committed or logged at more than Debug level.

**M3. Locks.**
- `assets._image_lock(d, name)` becomes a process-scoped lock over 256 stripes, keyed by `sha256(normcase(resolved d) + "\0" + casefold(name))`. Case aliases therefore share a lock (§11's case-aliasing).
- `_image_locks_held` orders by **stripe index**, collapsing two names that share a stripe.
- `sidecar_lock` uses the same striping.
- A contended lock past `LOCK_TIMEOUT` raises `StoreBusy`. The routes already answer that with 409.
- The order is unchanged: campaign lock, then name lock, then sidecar lock, then ingest/GC lock, then object stripe.

**M4. Surfaces.**
- `store/image_surfaces.py` lists every place images live, built from the path builders:
  - `assets.version_dir` per base (`characters`, `pcs`, every `entities.ENTITY_KINDS`, `greetings`);
  - the world and campaign library dirs;
  - the cover dirs;
  - collection manifests.
- It covers world and campaign roots alike, plus the two metadata-only occurrences:
  - a campaign description key for an inherited image;
  - a campaign bare `focus.json` over an inherited avatar.
- `tests/test_image_surfaces.py`'s roster asserts it matches. This is the "`image_store.SURFACES`" of the spec. It cannot live in `image_store`, which sits below `assets`.

**M5. Migration plan (dry run and real run share it).**
- **Inventory:** every legacy file of a storable name, and every metadata-only occurrence.
- **Sanitize and hash:** each file is sanitized in memory and hashed. The work map is `.cache/image-store/migration/map.json`, and losing it costs only time.
- **Decode:** each distinct sanitized stream is decoded once for pixel identity. A file that does not sniff or decode is untouched and reported.
- **Case aliases:** names in one directory that differ only by case are never both placed. The spelling that sorts first is placed, and the other is reported with its path.
- **Grouping:** by pixel id. The retained blob is chosen by most placements, then the smaller file, then the lowest byte hash.

**M6. Metadata merge** (§11.D), per object:
- **Descriptions:**
  - absent everywhere → absent;
  - one distinct non-empty text (with or without `""`) → that text;
  - only `""` → `""`;
  - several distinct texts → no `description`, and `description_conflicts: [{text, from}]`, where `from` is the occurrence path relative to the store root.
  - An object `description` already present counts as one of the texts.
- **Subjects:** unioned per scope. Reviewed-empty beside tagged gives tagged, and the disagreement is reported. Cross-world reference keys (stage-2 R11) stay in `subjects.json`, untouched.
- **Sources:** normalized and deduplicated.
- **Focus:** never merged. It moves to its own placement, or to an image-less occurrence override for a campaign `focus.json` over an inherited avatar.

**M7. Writes, then verify, then clean up** (§11.E–F). For each occurrence, under its campaign lock where it has one and always its name lock:
1. Re-stat the file and confirm the hashed identity; otherwise skip it until the next run.
2. Ingest (blob then sidecar), then merge the metadata with `image_store.update`.
3. Write the placement atomically, focus included.

**Verify:** the placement resolves to the intended object, the object to the intended blob, the blob re-hashes, `assets.path_in` returns the blob, and focus and description read back.
**Only then:**
- delete the legacy file, the migrated sidecar keys (in `descriptions.json`, `subjects.json`, `focus.json`) and any sidecar left empty;
- delete the legacy file's current-generation thumbnail entries. Their keys are computed from the file's relative path and stat **before** it is deleted, for every bucket in `THUMB_BUCKETS` and both encoders.

**Campaign writes:** every campaign written bumps `revision.bump(cid)` under its lock.

**Crash states:** a crash leaves legacy-only, both, or new-only — never a fourth state. A rerun is idempotent.

**M8. Collections and journals.**
- A format-1 manifest is rewritten to format 2 once all its members verify. Member **order and indices are kept**, including an entry for a member that no longer resolves. A missing member keeps its slot as the id the migration resolved for it. If it never resolved, the manifest is **left format 1** and reported.
- Leftover format-1 harvest journals are converted, or retired by renaming them to `.retired.json` and reporting them. Either way they stop holding the stage-3 C4 guard on.
- The `guard_write` code stays. It is inert where no format-1 state remains, and another device's legacy state may still need it.

**M9. Conflicts in the describe queue.**
- Undescribed rows gain `conflicts: [{text, from}]` when the object has `description_conflicts`.
- `image_descriptions.set_in`'s object write clears `description_conflicts` in the same `update`.
- `DescribeQueue.tsx` shows the competing texts as one-click choices.

**M10. GC** (§12).
- **Roots:**
  - every placement under every world and campaign root, through a **strict** walk (`image_refs.walk_strict`) that also reads `.promote.json` journals (their pre and post ids) and raises on any unparseable placement;
  - every manifest (both formats);
  - every harvest journal;
  - the migration work map;
  - every staged tree under `.world-staging` and `.module-staging`.
- **Candidates:** unreachable objects whose sidecar **and** blob mtimes are older than the grace period. The default is `30` days, and nothing below `7` is accepted. An object is a candidate only once it has also been unreachable on two scans at least 24 h apart, recorded in `.cache/image-store/gc.json`.
- **Nothing is deleted when:**
  - any placement, manifest or journal fails to parse;
  - a staging directory exists;
  - the store root changed during the scan.
- **Deletion:**
  - requires the token of a dry-run report from the same UTC day;
  - takes `image_ingest_gc_lock()` once, then each object's stripe;
  - re-checks reachability and mtime;
  - deletes the sidecar first, then the blob, then the blob-index entry and the object's thumbnails.
- **Nothing outside `image-store/` is ever deleted,** apart from those cache entries.
- **The report:** placement, object and blob counts; unreferenced objects; collectable versus grace-protected objects; exact reclaimable bytes.

**M11. UI.** Settings → Storage gains an "Image store" card under the location.
- **Migration:** "Check" runs a dry-run migrate and shows the report. "Migrate" runs the real migration after a confirm.
- **Clean up:** "Find unused images" runs a dry-run GC and shows counts and reclaimable bytes. "Delete N unused images" carries that report's token after a confirm.
- **While running:** a run in progress shows its state, and can be cancelled through the global run routes. A live run is rediscovered after navigation by listing `/api/runs`.

**M12. Accepted:**
- `.cache` is not excluded from sync (thumbnails rely on it syncing). Stage 3's C13 note is corrected: one device's harvest journals may or may not have reached another, so GC on a device without them can collect members of an unaccepted harvest. Unaccepted harvests are re-runnable.
- Maintenance never runs at startup.

## Global Constraints

- All stage-2 and stage-3 Global Constraints still hold: atomic writes, the `update` contract, imports, privacy, ratchets, the docs guard, and the frozen campaign never migrated (no test runs migrate on it, and its sweep stays unchanged).
- **Lock order (M3):** campaign lock → name lock → sidecar lock → `image_collection_job_lock` → `image_collection_lock` → ingest/GC lock → object stripe. GC takes the ingest/GC lock, **then** the stripes.
- **Lock domain:** `image_migration`'s public `cid`-taking mutators take `campaign_lock(cid)`, and it goes into `locks.DOMAIN_MODULES`. `image_gc` writes no campaign state and goes into `OUTSIDE_DOMAIN`, with its reason.
- **Revision:** every campaign the migration writes is bumped where it writes.
- **No CLI.** Maintenance is reachable only through its routes.
- **Tests:**
  - synthetic images only;
  - crash injection by monkeypatching a named step to raise;
  - every destructive test asserts that nothing outside `tmp_path` changed.
- **How to run things:**
  - Backend tests from `backend/`: `PYTHONPATH=src .venv/bin/python -m pytest <path> -q`.
  - Frontend from `frontend/`: `npx vitest run <path>` and `npx tsc --noEmit -p .`.
  - Full gate: `make check`. The only allowed failure is the root-only `test_atomic` one.

## Review Focus

1. **The only working copy is never deleted before its replacement verifies.**
   - Crash injection before the placement write, after it, and after cleanup.
   - A legacy file modified between hashing and writing is skipped, not overwritten.
   - A concurrent upload during migration is not lost.
2. **GC fails closed.**
   - One garbled placement anywhere, or a staging directory, or a root swap mid-scan: no delete.
   - An ingest racing a delete is never left with a sidecar but no blob.
   - A delete without a same-day dry-run token is refused.
3. **Collections keep their indices through migration.**
   - Member URLs cached as immutable before migration still name the same picture after.
   - A manifest with an unresolvable member stays format 1.
4. **Striped locks.**
   - Two names sharing a stripe, held together, do not deadlock.
   - Two processes (simulated by two lock instances against the same store key) serialize.
   - Case aliases share a lock.
5. **The second maintenance start** is refused with `run_in_flight`. A data-dir move is refused while maintenance runs. Cancelling stops at the next item boundary and still writes a report.

---

### Task 1: The `maintenance` run class, the thread runner, reports

**Files:**
- Modify:
  - `backend/src/grimoire/routes/runs.py` (`RunClass`, `exclusion_key`, `run_maintenance`)
  - `backend/src/grimoire/runner.py` (only if class sets live there)
  - `frontend/src/api/stream.ts` (`RunHandle.cls` union)
- Create: `backend/src/grimoire/store/maintenance_reports.py` (`write(run_id, report) -> Path`, `read(run_id) -> dict | None`, `reports_dir() -> Path`)
- Test: `backend/tests/test_maintenance_runs.py`, `backend/tests/test_runs_registry.py`

**Interfaces:**
- **Produces:** `runs.run_maintenance(app, kind: str, attempt: str, work_sync: Callable[[Run], dict]) -> dict` returns `{"run": payload}`. Here `work_sync` returns the report, and the runner persists it.

- [ ] **Step 1: Write the failing tests.**
  - `test_maintenance_has_its_own_key_and_refuses_a_second_start` (409 `run_in_flight`).
  - `test_maintenance_does_not_refuse_a_model_refresh`.
  - `test_a_data_dir_move_is_refused_while_maintenance_runs`.
  - `test_cancel_stops_at_the_next_item_and_still_reports`.
  - `test_the_report_is_persisted_and_readable_after_reap`.
  - `test_maintenance_is_not_notifying`.
- [ ] **Step 2:** run them; they fail.
- [ ] **Step 3:** implement.
- [ ] **Step 4:** run the files plus `tests/test_runs_routes.py tests/test_draft_runs.py tests/test_scene_freeze.py`; they pass.
- [ ] **Step 5:** commit: `feat(runs): a maintenance run class for image-store upkeep`.

### Task 2: Process-scoped, striped per-name image locks

**Files:**
- Modify: `backend/src/grimoire/store/assets.py` (`_image_lock`, `_image_locks_held`, `sidecar_lock`, `_locks_if_free`), `backend/src/grimoire/store/locks.py` (a `image_name_lock(d, name)` builder and `IMAGE_NAME_STRIPES = 256`)
- Test: `backend/tests/test_image_name_locks.py`, plus the existing lock-dependent tests listed in the code map (adapt them only where they relied on a name-specific object)

**Interfaces:**
- **Produces:**
  - `locks.image_name_lock(d: Path, name: str) -> _ProcessScopedLock`;
  - `assets._image_locks_held(d, *names)`, ordered by stripe index with duplicate stripes collapsed.

- [ ] **Step 1: Write the failing tests.**
  - `test_case_aliases_share_a_lock`.
  - `test_names_on_one_stripe_hold_together_without_deadlock`.
  - `test_two_lock_instances_on_one_store_serialize` (the process-scoped file lock).
  - `test_contention_past_the_timeout_raises_store_busy`.
  - `test_recovery_skips_rather_than_waits_on_a_busy_stripe`.
- [ ] **Step 2:** run them; they fail.
- [ ] **Step 3:** implement.
- [ ] **Step 4:** run the new file plus `tests/test_assets_store.py tests/test_image_descriptions_store.py tests/test_characters_store.py tests/test_overlay.py tests/test_lock_order_guard.py tests/test_docs_guard.py`; they pass.
- [ ] **Step 5:** commit: `feat(images): per-name image locks are process-scoped and striped`.

### Task 3: The surface roster and the migration plan (dry run)

**Files:**
- Create: `backend/src/grimoire/store/image_surfaces.py`, `backend/src/grimoire/store/image_migration.py` (inventory, sanitize/hash, decode, group, merge plan, report)
- Modify: `backend/tests/test_image_surfaces.py` (assert that the src roster matches the test roster), `store/__init__.py`, `tests/store_api_baseline.json`, `locks.py` (classification)
- Test: `backend/tests/test_image_migration_plan.py`

**Interfaces:**
- **Produces:**
  - `image_surfaces.occurrences(home: Path) -> Iterator[Occurrence]`, where `Occurrence` has `kind`, `scope`, `dir`, `name`, `path | None` and `metadata_only: bool`.
  - `image_migration.plan(*, cancel: Callable[[], bool]) -> MigrationPlan`.
  - `image_migration.report(plan) -> dict`, with the §11 report fields:
    - `legacy_files`, `unique_streams`, `unique_images`, `exact_duplicates`, `pixel_variants`;
    - `descriptions_merged`, `description_conflicts`, `subject_disagreements`;
    - `untouched: [{path, reason}]`, `case_aliases: [{path}]`;
    - `bytes_before`, `bytes_after`, `bytes_reclaimed`.

- [ ] **Step 1: Write the failing tests.**
  - `test_roster_matches_the_surface_tests`.
  - `test_exact_and_metadata_only_variants_collapse` (a card avatar with `chara` text versus the same pixels without it).
  - `test_conflicting_descriptions_are_planned_as_conflicts`.
  - `test_campaign_key_over_inherited_art_is_an_occurrence`.
  - `test_a_corrupt_file_is_untouched_and_the_rest_plan`.
  - `test_case_aliases_place_only_the_first_spelling`.
  - `test_retained_blob_choice` (most placements, then the smaller file, then the lowest hash).
  - `test_dry_run_writes_nothing_outside_cache`.
- [ ] **Step 2:** run them; they fail.
- [ ] **Step 3:** implement.
- [ ] **Step 4:** run the files plus the guards (`import`, `lock_domain`, `store_api_baseline`, `paths`, `atomic`); they pass.
- [ ] **Step 5:** commit: `feat(images): migration inventory and dry-run plan`.

### Task 4: Migration writes, verification and cleanup

**Files:**
- Modify: `backend/src/grimoire/store/image_migration.py` (`run(*, dry_run: bool, cancel) -> dict`), `backend/src/grimoire/store/thumbs.py` (`legacy_keys(rel: str, st: os.stat_result) -> list[Path]` for every bucket and both encoders)
- Test: `backend/tests/test_image_migration_run.py`

- [ ] **Step 1: Write the failing tests.**
  - `test_migration_places_merges_verifies_and_cleans`.
  - `test_crash_before_ref_write_leaves_legacy_only`, `test_crash_after_ref_write_leaves_both_and_rerun_cleans` and `test_crash_after_cleanup_is_new_only` (Review Focus 1).
  - `test_a_file_changed_after_hashing_is_skipped`.
  - `test_a_concurrent_upload_during_migration_is_kept`.
  - `test_rerun_is_idempotent`.
  - `test_focus_moves_to_its_placement_and_campaign_focus_to_an_override`.
  - `test_cross_world_subject_keys_stay_in_the_sidecar`.
  - `test_campaigns_written_are_revision_bumped`.
  - `test_legacy_thumbnails_are_removed`.
  - `test_format_1_manifest_becomes_format_2_with_indices_kept` (Review Focus 3).
  - `test_a_manifest_with_an_unresolvable_member_stays_format_1`.
  - `test_format_1_journals_convert_or_retire`.
  - `test_emptied_sidecars_are_deleted`.
  - `test_the_frozen_campaign_sweep_is_unchanged_after_migrating_a_copy_elsewhere`. This guards that migration code does not change readers of legacy stores; the fixture itself is never migrated.
- [ ] **Step 2:** run them; they fail.
- [ ] **Step 3:** implement.
- [ ] **Step 4:** run the files plus `tests/test_image_surfaces.py tests/test_image_descriptions_store.py tests/test_image_subjects_store.py tests/test_image_collections.py tests/test_frozen_campaign.py`; they pass.
- [ ] **Step 5:** commit: `feat(images): migrate legacy images, verify, then clean up`.

### Task 5: Description conflicts in the describe queue

**Files:**
- Modify: `backend/src/grimoire/store/image_descriptions.py` (rows carry `conflicts`; `set_in` clears `description_conflicts`), `frontend/src/components/DescribeQueue.tsx`, `frontend/src/api/types.ts`
- Test: `backend/tests/test_image_descriptions_store.py`, `frontend/src/components/DescribeQueue.test.tsx`

- [ ] **Step 1: Write the failing tests.**
  - `test_a_conflicted_object_is_queued_with_its_texts`.
  - `test_resolving_a_conflict_writes_the_description_and_clears_the_list`.
  - Frontend: `offers each conflicting text as a choice`.
- [ ] **Step 2:** run them; they fail.
- [ ] **Step 3:** implement.
- [ ] **Step 4:** run them, plus `tsc` and `check-eslint`; they pass.
- [ ] **Step 5:** commit: `feat(images): the describe queue resolves migration conflicts`.

### Task 6: Garbage collection

**Files:**
- Create: `backend/src/grimoire/store/image_gc.py`
- Modify: `backend/src/grimoire/store/image_refs.py` (`walk_strict(root) -> Iterator[tuple[Path, Ref]]`, raising `ImageRefParseError` on an unparseable placement or journal), `locks.py` (`OUTSIDE_DOMAIN` entry), `store/__init__.py`, `tests/store_api_baseline.json`
- Test: `backend/tests/test_image_gc.py`

**Interfaces:**
- **Produces:**
  - `image_gc.scan(*, cancel) -> dict`, a dry-run report including `token`;
  - `image_gc.collect(token: str, *, cancel) -> dict`.
  - Constants: `GRACE_DAYS_DEFAULT = 30`, `GRACE_DAYS_MIN = 7`, `SCAN_GAP_HOURS = 24`.

- [ ] **Step 1: Write the failing tests.**
  - `test_referenced_and_multiply_referenced_images_are_kept`.
  - `test_recent_orphans_are_grace_protected`.
  - `test_an_old_orphan_is_collectable_only_after_two_scans_a_day_apart`.
  - `test_any_parse_failure_aborts` (a placement and a journal).
  - `test_a_staging_tree_aborts` and `test_a_root_swap_mid_scan_aborts`.
  - `test_a_concurrent_ingest_is_never_left_dangling`: the ingest re-touches the sidecar between the scan and the delete, so it is kept.
  - `test_delete_needs_a_same_day_token`.
  - `test_nothing_outside_image_store_is_deleted`.
  - `test_collection_manifests_journals_and_staging_are_roots`.
- [ ] **Step 2:** run them; they fail.
- [ ] **Step 3:** implement.
- [ ] **Step 4:** run the file plus the guards; it passes.
- [ ] **Step 5:** commit: `feat(images): a two-scan, fail-closed garbage collector`.

### Task 7: Maintenance routes and the Settings card

**Files:**
- Create: `backend/src/grimoire/routes/maintenance.py`: `POST /maintenance/images/migrate` (body `{"dry_run": bool}`), `POST /maintenance/images/gc` (body `{"dry_run": bool, "token": str | None}`), `GET /maintenance/images/reports/{run_id}`; all answer 202 with a run and require `X-Grimoire-Attempt`
- Modify: `backend/src/grimoire/routes/__init__.py`, `frontend/src/api/client.ts` (`startImageMigration`, `startImageGc`, `getMaintenanceReport`, `findLiveMaintenance`), `frontend/src/api/types.ts`, `frontend/src/routes/ConfigView.tsx` (Storage section)
- Create: `frontend/src/components/ImageStoreCard.tsx`, `frontend/src/components/ImageStoreCard.test.tsx`
- Test: `backend/tests/test_maintenance_routes.py`, `backend/tests/test_route_order.py`

- [ ] **Step 1: Write the failing tests.**
  - Backend:
    - `test_migrate_dry_run_returns_a_run_and_a_report`;
    - `test_gc_delete_without_token_is_400`;
    - `test_second_start_is_409`;
    - `test_report_route_404_for_unknown_run`.
  - Frontend:
    - `check shows the dry-run report`;
    - `migrate asks to confirm first`;
    - `find unused images then delete carries the token`;
    - `a live run is rediscovered on mount`;
    - `cancel calls the global cancel route`.
- [ ] **Step 2:** run them; they fail.
- [ ] **Step 3:** implement.
- [ ] **Step 4:** run the files plus `tests/test_route_order.py`, `tsc` and `check-eslint`; they pass.
- [ ] **Step 5:** commit: `feat(images): Settings runs image migration and clean-up`.

### Task 8: Docs, CLAUDE.md, spec amendments, gate

**Files:**
- Modify:
  - **The spec:** add "### Stage-4 amendments" with M1–M12, one bullet each.
  - **`docs/store-guarantees.md`:**
    - the `maintenance` class;
    - the striped name locks, in the lock tables and the contended-lock list;
    - replace "No collector exists yet" and "Nothing is collected yet" with what GC promises;
    - the grace period and the two scans.
  - **`CLAUDE.md`:**
    - "Detached runs": the class list and handler count, recounted from the code (it has already drifted);
    - the images note: migration and GC exist and are started from Settings.
  - **Docstrings:** `assets.delete_in` and `thumbs.py`'s legacy-entry note.
- Test: `backend/tests/test_docs_guard.py`, in its style:
  - CLAUDE.md names every run class in `runs.RunClass`;
  - `store-guarantees` names `image_name_lock` and `IMAGE_NAME_STRIPES`, and both exist.

- [ ] **Step 1:** write the guard assertions; they fail.
- [ ] **Step 2:** write the docs; the guard passes.
- [ ] **Step 3:** run the full gate, `make check PY=backend/.venv/bin/python`.
- [ ] **Step 4:** commit: `docs(images): stage-4 amendments, guarantees and run classes`.
