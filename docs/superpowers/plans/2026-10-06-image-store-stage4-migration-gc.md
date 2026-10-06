# Content-Addressed Image Store — Stage 4 (Migration and GC) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** two maintenance operations, both explicit, restartable and fail-closed:
- **Migration** moves every legacy image file, sidecar entry and format-1 collection into the image store. It verifies the new copy and only then removes the legacy copy.
- **Collection** (GC) removes images that nothing anywhere reaches.

Both are started from Settings → Storage. Each runs as a single detached run and leaves a report.

**Architecture:**
- **Run class.** A new `maintenance` run class on `("global",)`, with its own exclusion key. Its sync work runs in a worker thread that the run outlives, and it checks a cancel flag between items.
- **Locks.** The per-name image and sidecar locks become process-scoped and striped.
- **New store modules:**
  - `image_surfaces`: the roster of where images live.
  - `image_migration`: inventory, plan, write, verify, fold, clean up.
  - `image_gc`: strict roots, blob refcounts, per-device sightings, deletion in batches.
- **Pinned root.** Every destructive step builds its paths from the store root captured at the start of the run, and re-checks that root before deleting anything.

**Tech Stack:** Python 3.11+, FastAPI, anyio, pytest; React + TypeScript, vitest.

**Spec:** `docs/superpowers/specs/2026-10-05-content-addressed-image-store-design.md`, stage 4 of §14. Read §8, §10–§13 and every amendment section (stages 1–3). A code map is at `.superpowers/sdd/stage4-notes/code-map.md`.

**Precondition:** stages 2 and 3 are complete on this branch:
- `image_store.iter_ids`, `read_fresh` and `update -> bool`;
- `image_scopes` and `image_usage`;
- format-2 collections, `has_format1` and the `catalog_with_slots` targets;
- the stage-2 description and subject façades.

## Stage-4 rulings (the spec gains them in Task 8)

### Runs

**M1. The `maintenance` run class.**
- **Class:** `RunClass` gains `"maintenance"`.
  - `exclusion_key` gives it `"global\x00image-maintenance"`, keyed on the class, so no other global run is refused by it.
  - It is neither notifying nor attachable.
- **Starting it:** `runs.run_maintenance(app, kind, attempt, work_sync)` reserves the run:
  - a second start answers 409 `run_in_flight`;
  - a store move in progress answers 409 `busy`.
- **Running it:**
  - `work_sync(run)` runs through `anyio.to_thread.run_sync`, with the await **shielded**: the run stays live, and keeps its exclusion and its hold on data-dir moves, until the thread returns.
  - Cancellation is cooperative through `run.cancel_requested`, and **`abandon_on_cancel` is never used**.
  - `work_sync` writes its report in its own `finally`, so a cancelled or failed run still leaves one.
  - On lifespan shutdown, `cancel_requested` is set on every live maintenance run before the task group is cancelled.
- **Routes:** the routes that reserve are `def`, not `async def`, following CLAUDE.md's portal rule.

**M2. Reports.**
- Each run atomically writes `.cache/image-store/reports/<run id>.json` and also returns the report as its result.
- `run_id` is validated against `^[0-9a-f]{32}$`.
- Reports older than 30 days are pruned when the next one is written.
- A dry run and a real run produce the same shape (§11).

**M3. Operations refused while maintenance runs.**
- Each operation below checks `runs.maintenance_live(app)` under the registry lock:
  - `fork_world`, `delete_world`, `delete_campaign` and campaign fork answer 409 `maintenance_running`;
  - bundle export answers 409;
  - the scheduled backup skips its turn and logs it.
- A synced marker, `.cache/image-store/maintenance.json`, holds `{device, run, heartbeat}` and is refreshed every 30 s. A device that finds a marker whose heartbeat is younger than 5 minutes, written by another device, refuses to start maintenance (409 `maintenance_elsewhere`). The device id is a random id kept in `paths.home()/.cache/device-id`; it is local and never committed.

### Store root

**M4. The store root is pinned.**
- A run captures `root = paths.home().resolve()` when it starts, and builds every path from `root`. That includes object and blob paths, through `image_store`'s path builders taking an explicit root, which this task adds.
- Before every destructive step (a legacy unlink, a key delete, a sidecar or blob unlink), the run asserts that `paths.home().resolve() == root`. On a mismatch it stops and records a report entry; nothing is deleted after that point.

### Locks

**M5. Locks.**
- **Two families:**
  - name locks: `locks.image_name_lock(d, name)`, domain `image-names`;
  - sidecar locks: `locks.image_sidecar_lock(d, filename)`, domain `image-sidecars`.
- **Striping:** each family has `IMAGE_NAME_STRIPES = 256` stripes, keyed by `sha256(normcase(resolved d) + "\0" + casefold(name))`, so case aliases share a lock. There is one lock instance per `(store key, domain, stripe)`.
- **Order:** campaign → `image_collection_job_lock` → `image_collection_lock` → name → sidecar → ingest/GC → object stripe.
- **Holding several:**
  - `_image_locks_held` acquires a family's stripes sorted and deduplicated, all up front;
  - `set_in` acquires its two sidecar stripes (the edited directory's and the fallback or also-clear directory's) together, sorted, up front;
  - a nested heal (`_heal_stranded_promotion` reached while a name is held) is non-blocking: it skips when it cannot get the lock;
  - `_locks_if_free` never raises: a busy lock and an `OSError` both mean "not free".
- **Contention:**
  - Contention past `LOCK_TIMEOUT` raises `StoreBusy`, whose message no longer says "another process".
  - Multi-step callers that can now stop part-way have each been confirmed safe to re-run (Task 2 lists and tests them): `copy_slots`, `sync._copy_tree`, `overlay.copy_record_dir_down`, `characters._replace_gallery`, and bundle containment.

### Migration

**M6. Surfaces.** `store/image_surfaces.py` builds the roster from the code's own path builders, for world and campaign roots:
- `assets.version_dir` per base: `characters`, `pcs`, every `entities.ENTITY_KINDS`, `greetings`;
- the library dirs and the cover dirs;
- collection manifests.

Plus **metadata-only occurrences**:
- (a) a campaign description key for an inherited image that the overlay would show (not tombstoned, not hidden, campaign not detached);
- (b) a campaign bare `focus.json` over a visible inherited avatar;
- (c) a description or subject key whose name has an image-bearing placement in the same directory;
- (d) a `focus.json` beside an avatar placement.

`tests/test_image_surfaces.py` asserts that the src roster matches the test roster.

**M7. Planning.** The dry run and the real run share the plan; the dry run writes nothing but its report.
- **Inventory:** each storable legacy file, selected the way `assets._legacy_path` selects it. Library and cover dirs use `supported_only=True`. Other extensions and other siblings are left untouched and reported.
- **Hashing:** each file is sanitized in memory, hashed, and decoded once per distinct sanitized stream. A file that does not sniff or decode is left untouched and reported.
- **Case aliases:** names compared case-folded against **both** existing placements and legacy names. For any alias set with more than one member, nothing is placed; everything is left and reported.
- **Grouping:** by pixel id. A **new** object's retained blob is chosen by most placements, then the smaller file, then the lowest byte hash. An existing object's blob is never replaced.
- **Work map:** the plan's pending entries are kept in `.cache/image-store/migration/map.json`.

**M8. Writing one occurrence.**
1. `_recover_promotion(d)` first, as spec §8 requires of every writer.
2. Re-read the exact file and re-hash its sanitized bytes; ingest them. This is done **outside** the campaign lock, so turns are never kept waiting behind decodes. Record a `(dev, ino, size, mtime_ns)` snapshot.
3. Under the campaign lock (where there is one), then the name lock:
   - re-check the snapshot;
   - **if an image-bearing placement exists** (resolving or not), never overwrite it. The legacy file is deleted only when its current bytes `identify` to the placement's id. Otherwise both stay and "legacy differs from placement" is reported.
   - **otherwise**, write the placement atomically, with focus;
   - then verify: the placement resolves to the intended object, which resolves to the intended blob; the blob re-hashes; `path_in` returns it; focus reads back;
   - then identity-check-unlink exactly the hashed file;
   - then delete its legacy thumbnails, with keys computed from its relative path and pre-delete stat for every bucket and both encoders.
4. Bump `revision.bump(cid)` for each campaign written, under its lock.

**M9. Folding metadata** happens only **after** the target placement has verified. It runs under that directory's sidecar locks for `descriptions.json` and `subjects.json`, held from the read through the delete.
- **Read at fold time:** values are always re-read at fold time, never taken from the work map.
- **Descriptions:**
  - The texts considered are: the object's `description` (`from: "object"`), its existing `description_conflicts`, and the current key.
  - The rules are §11.D's. Conflict entries are never dropped except by the describe queue.
  - `description_conflicts` is capped at 20 entries; the cap is reported.
  - A key whose value is not a string, or is longer than `MAX_DESCRIPTION`, is left in place and reported.
- **Subjects:** unioned per scope, and any disagreement is reported. Keys that point across worlds (stage-2 R11) are not folded.
- **Deleting a key** is compare-and-delete: only if it still equals the value folded; otherwise the fold runs again.
- **Metadata-only occurrences** (M6 a–d) fold only into a target whose placement has verified. If the target was left untouched, the key stays.

Crash states are legacy-only, both, or new-only. A rerun is idempotent.

**M10. Collections and journals.**
- **When a format-1 manifest converts to format 2:** only when every member is either (a) a library placement that resolves, or (b) a legacy member file whose `sha256` matches its name and which migration has placed. Otherwise it stays format 1 and is reported.
- **On conversion:**
  - member order and indices are kept;
  - the members' library placements are **kept**, so library URLs keep working;
  - every greeting `subjects.json` key naming a converted member's library URL is rewritten in the same step, through `catalog_with_slots` targets: to the member URL for a cross-world key, or folded into the object for a same-world key.
- **Harvest journals:** convertible ones are converted. Only unaccepted, unmappable ones are retired, renamed to `<job>.json.retired`, which is outside the `*.json` glob.
- `has_format1` and GC read the raw journal directory, which tolerates a deleted world.
- The `guard_write` code stays.

**M11. Conflicts in the describe queue.**
- **Rows:** the record, world-library and campaign-library queues all add `conflicts: [{text, from}]` to their rows.
- **Clearing:** `image_descriptions.set_in`'s object write clears `description_conflicts` in the same `update`.
- **Other writers:** `world_bundle`'s fill-when-absent and `merge_projection` treat a non-empty `description_conflicts` as "has a description".

### Collection (GC)

**M12. Roots.**
- **`image_refs.walk_strict(root)` (new):**
  - parses every regular file in every `image-refs/` folder, whatever its stem (so sync-conflict copies count);
  - takes ids from `.promote.json` journals (pre and post);
  - parses atomic temp files when it can and ignores them when it cannot;
  - raises on: an unparseable non-temp file, an unknown `format`, any walk error (`os.walk(onerror=raise)`), and any symlink under a root.
- **Manifests and harvest journals** are walked with the same strictness.
- **Staging:** staged work dirs under `.world-staging` and `.module-staging` are walked strictly as roots.
- **The migration work map:** only its **pending** entries (ingested, not yet verified) are roots. Entries are pruned on verify, and the map is deleted when a run completes.

**M13. Candidates.** An object is a candidate only when **all** of these hold:
- it is unreachable from every root;
- its sidecar's mtime is older than the grace period;
- this device's **first unreachable sighting** of it is at least `GRACE_DAYS` old;
- it was seen unreachable again on a scan at least 24 h after that sighting.

Sighting details:
- Sightings live in `.cache/image-store/gc/<device>.json` and reset when the object is seen reachable.
- An mtime or a sighting in the future is never collectable, and is flagged as clock skew.
- Grace: `GRACE_DAYS_DEFAULT = 30`, and nothing below `GRACE_DAYS_MIN = 7` is accepted.
- **Accepted residual risk:** a device offline longer than the grace period can bring back a placement whose image was already collected.

**M14. Blobs.**
- A blob is deleted only when no surviving readable sidecar names it. The `blob → [ids]` map is built over all sidecars and re-checked under the lock. Any unreadable sidecar keeps every blob: fail closed, and reported.
- An orphan blob, one no readable sidecar names, is a candidate under the same grace and sighting rules.
- A shared blob is excluded from the reclaimable bytes.
- A blob-index entry is deleted only if it names the collected id.

**M15. Deleting.**
- **No deletion at all when:**
  - any root fails to parse;
  - a staged work dir is younger than 1 h;
  - the root changes (M4).
  
  The report names each blocking path.
- **The token:**
  - comes from a persisted dry-run report on this device: `{token, ids, root, device, scanned_at}`;
  - is single-use, and valid for 24 h;
  - a report that synced in from another device is refused.
- **What gets deleted:** `collect(token)` deletes at most that report's collectable ids, intersected with a fresh strict re-walk done outside the lock.
- **Batches:**
  - up to 100 objects, or 2 s, per hold of `image_ingest_gc_lock()`;
  - within a batch, each object's stripe;
  - re-check mtime, root, reachability (by the fresh walk) and the blob refcount;
  - delete the sidecar, then (refcount permitting) the blob, the index entry and the object's thumbnails.
- **Nothing outside `image-store/`** is deleted, apart from those cache entries.

### UI and documentation

**M16. UI.** Settings → Storage → "Image store" card.
- **Migration:** "Check" (a dry-run migrate shows the report), then "Migrate" after a confirm.
- **Collection:**
  - "Find unused images" runs a dry-run GC. The report shows what is collectable now and what becomes collectable on which date.
  - "Delete N unused images" carries the token, after a confirm.
- **While running:** a live run is rediscovered by listing `/api/runs`, and can be cancelled. Blocking paths and `maintenance_elsewhere` are shown verbatim.

**M17. Recorded in the amendments:**
- `.cache` syncs, which corrects stage-3 C13. Hence the device-keyed GC state (M13) and the maintenance marker (M3).
- Maintenance never runs at startup.
- **The frozen campaign is never migrated:** no test passes its `home/` to migrate or collect.

## Global Constraints

- **Carried over:** every stage-2 and stage-3 Global Constraint still holds: atomic writes, the `update` contract, imports, privacy, ratchets, the docs guard, and the frozen snapshot unchanged.
- **Lock order:** exactly M5's.
- **Lock domain:**
  - `image_migration`'s public `cid`-taking mutators take `campaign_lock(cid)`, and the module goes in `locks.DOMAIN_MODULES`.
  - `image_gc` goes in `OUTSIDE_DOMAIN`, with its reason: it writes no campaign state.
- **Revision:** every campaign the migration writes is bumped where it is written.
- **No CLI.**
- **Tests:**
  - synthetic images only;
  - crash injection by monkeypatching a named step to raise;
  - every destructive test asserts that nothing outside `tmp_path` changed, and that a run whose root flips deletes nothing.
- **How to run things:**
  - Backend tests from `backend/`: `PYTHONPATH=src .venv/bin/python -m pytest <path> -q`.
  - Frontend from `frontend/`: `npx vitest run <path>` and `npx tsc --noEmit -p .`.
  - Full gate: `make check`. The only allowed failure is the root-only `test_atomic` one.

## Review Focus

1. **The only working copy is never deleted.** Each of these has a test:
   - crash injection before the placement write, after it, and after cleanup;
   - a root flip;
   - a legacy file that differs from an existing placement;
   - a file changed between hashing and deleting;
   - a description or subject edited during the fold;
   - a concurrent upload.
2. **GC fails closed.** Each of these has a test:
   - a sync-conflict copy of a placement is a root;
   - an unreadable directory, an unknown format and a symlink each abort;
   - a shared blob survives;
   - a late-synced placement inside the grace window survives;
   - a future mtime is never collectable;
   - a token is single-use and device-bound;
   - an ingest racing deletion keeps both its sidecar and its blob.
3. **Collections** keep their indices, their library URLs and their subject tags through conversion.
4. **Striped locks:**
   - names and sidecars crossing stripes do not deadlock, even with the stripe function forced to collide;
   - case aliases share a lock;
   - two instances serialize.
5. **Runs:**
   - a second start is refused, on this device and from another device;
   - the data-dir move, fork, delete and export are refused while maintenance runs;
   - cancel and shutdown wait for the thread and still write the report.

---

### Task 1: The `maintenance` run class, the thread runner, reports, refusals

**Files:**
- Modify:
  - `backend/src/grimoire/routes/runs.py`: `RunClass`, `exclusion_key`, `run_maintenance`, `maintenance_live`
  - `backend/src/grimoire/runner.py`: the shielded thread await, and setting cancel on shutdown
  - `backend/src/grimoire/main.py`: lifespan shutdown
  - the M3 refusals in `store/worlds/lifecycle.py`, `store/campaigns/lifecycle.py`, `store/fork.py`, the bundle export route and `backups.run_scheduled`
  - `frontend/src/api/stream.ts`: the `RunHandle.cls` union
- Create: `backend/src/grimoire/store/maintenance_reports.py` (`write`, `read`, `prune`, `device_id`, the `marker` heartbeat helpers)
- Test: `backend/tests/test_maintenance_runs.py`

- [ ] **Step 1: Write the failing tests.**
  - `test_maintenance_has_its_own_key_and_refuses_a_second_start`.
  - `test_maintenance_does_not_refuse_a_model_refresh`.
  - `test_a_data_dir_move_fork_delete_and_export_are_refused_while_maintenance_runs`.
  - `test_cancel_waits_for_the_thread_and_still_reports`.
  - `test_shutdown_sets_cancel_and_waits`.
  - `test_the_exclusion_holds_until_the_thread_returns`.
  - `test_another_devices_live_marker_refuses_a_start`.
  - `test_the_report_survives_reap_and_old_reports_are_pruned`.
  - `test_maintenance_is_not_notifying`.
- [ ] **Step 2:** run them; they fail.
- [ ] **Step 3:** implement.
- [ ] **Step 4:** run the file, plus `tests/test_runs_routes.py tests/test_runs_registry.py tests/test_draft_runs.py tests/test_scene_freeze.py tests/test_world_fork.py tests/test_fork_store.py tests/test_backups_store.py`; they pass.
- [ ] **Step 5:** commit: `feat(runs): a maintenance run class for image-store upkeep`.

### Task 2: Process-scoped, striped name and sidecar locks

**Files:**
- Modify:
  - `backend/src/grimoire/store/locks.py`: `image_name_lock`, `image_sidecar_lock`, `IMAGE_NAME_STRIPES`, and one instance per stripe
  - `backend/src/grimoire/store/assets.py`: `_image_lock`, `_image_locks_held`, `sidecar_lock`, `_locks_if_free`, the non-blocking nested heal
  - `backend/src/grimoire/store/image_descriptions.py`: `set_in` acquires its sidecar stripes up front
- Test: `backend/tests/test_image_name_locks.py`, plus the existing lock-dependent tests from the code map (adapt them only where they relied on a name-specific object)

- [ ] **Step 1: Write the failing tests.**
  - `test_case_aliases_share_a_lock`.
  - `test_forced_stripe_collisions_do_not_deadlock`: monkeypatch the stripe function so two threads cross name, sidecar and nested-heal patterns, and assert neither raises `StoreBusy`.
  - `test_two_instances_serialize_across_the_file_lock`.
  - `test_locks_if_free_never_raises`.
  - `test_each_multi_step_caller_is_safe_to_rerun_after_store_busy`: covers `copy_slots`, `_copy_tree`, `copy_record_dir_down`, `_replace_gallery` and bundle containment.
- [ ] **Step 2:** run them; they fail.
- [ ] **Step 3:** implement.
- [ ] **Step 4:** run the new file, plus `tests/test_assets_store.py tests/test_image_descriptions_store.py tests/test_characters_store.py tests/test_overlay.py tests/test_lock_order_guard.py tests/test_world_bundle.py tests/test_docs_guard.py`; they pass.
- [ ] **Step 5:** commit: `feat(images): per-name and sidecar locks are process-scoped and striped`.

### Task 3: Surfaces, the pinned root, and the dry-run plan

**Files:**
- Create:
  - `backend/src/grimoire/store/image_surfaces.py`
  - `backend/src/grimoire/store/image_migration.py`: `plan`, `report`
- Modify:
  - `backend/src/grimoire/store/image_store.py`: path builders that take an optional explicit root
  - `backend/tests/test_image_surfaces.py`: the roster match
  - `store/__init__.py`, `tests/store_api_baseline.json`, `locks.py`
- Test: `backend/tests/test_image_migration_plan.py`

**Interfaces:**
- **`image_surfaces`:** `occurrences(root: Path) -> Iterator[Occurrence]`. An `Occurrence` has `kind`, `scope`, `dir`, `name`, `path | None` and `metadata_only`, the last one tagged a–d per M6.
- **`image_migration`:**
  - `plan(root, *, cancel) -> MigrationPlan`.
  - `report(plan) -> dict`, with the §11 fields plus `untouched`, `case_aliases`, `legacy_differs`, `overcap_keys` and `conflicts_capped`.

- [ ] **Step 1: Write the failing tests.**
  - `test_roster_matches_the_surface_tests`.
  - `test_exact_and_metadata_only_variants_collapse` (a `chara`-chunk avatar, against the same pixels without the chunk).
  - `test_all_four_metadata_only_occurrence_kinds_are_inventoried`.
  - `test_a_tombstoned_or_hidden_campaign_key_is_not_an_occurrence`.
  - `test_a_corrupt_file_is_untouched_and_the_rest_plan`.
  - `test_case_aliases_against_refs_and_files_place_nothing`.
  - `test_extra_siblings_and_unsupported_extensions_are_untouched`.
  - `test_retained_blob_choice_for_new_objects_only`.
  - `test_dry_run_writes_nothing_but_its_report`.
- [ ] **Step 2:** run them; they fail.
- [ ] **Step 3:** implement.
- [ ] **Step 4:** run them plus the guards; they pass.
- [ ] **Step 5:** commit: `feat(images): migration inventory and dry-run plan`.

### Task 4: Migration writes, verify, fold, clean up

**Files:**
- Modify:
  - `backend/src/grimoire/store/image_migration.py`: `run(root, *, dry_run, cancel) -> dict`
  - `backend/src/grimoire/store/thumbs.py`: `legacy_keys(rel, st) -> list[Path]`
  - `backend/src/grimoire/store/image_collections.py` / `image_collection_imports.py`: the conversion helpers (M10)
- Test: `backend/tests/test_image_migration_run.py`

- [ ] **Step 1: Write the failing tests.**
  - `test_migration_places_verifies_folds_and_cleans`.
  - Crash and root safety: `test_crash_before_ref_write_leaves_legacy_only`, `test_crash_after_ref_write_leaves_both_and_rerun_cleans`, `test_crash_after_cleanup_is_new_only`, `test_a_root_flip_deletes_nothing_in_either_root`.
  - Existing data is never overwritten: `test_a_legacy_file_differing_from_an_existing_placement_is_kept_and_reported`, `test_an_existing_unarrived_placement_is_never_overwritten`.
  - Concurrent changes: `test_a_file_changed_after_hashing_is_skipped`, `test_an_edit_during_the_fold_is_not_lost` (descriptions and subjects), `test_a_concurrent_upload_during_migration_is_kept`.
  - `test_rerun_is_idempotent_and_keeps_existing_conflicts`.
  - Metadata-only folds: `test_overcap_and_non_string_keys_stay`, `test_metadata_only_keys_fold_only_into_verified_targets`.
  - `test_focus_moves_to_its_placement_and_campaign_focus_to_an_override`.
  - `test_cross_world_subject_keys_stay_in_the_sidecar`.
  - `test_campaigns_written_are_revision_bumped`.
  - `test_legacy_thumbnails_are_removed`.
  - Collections: `test_format_1_manifest_converts_keeping_indices_library_urls_and_tags`, `test_a_member_whose_sha_mismatches_its_name_keeps_format_1`.
  - `test_journals_convert_or_retire_out_of_the_glob`.
  - `test_emptied_sidecars_are_deleted`.
  - `test_the_work_map_holds_only_pending_entries_and_is_removed_on_completion`.
  - `test_no_test_migrates_the_frozen_campaign`: an AST or grep guard over `tests/` asserting that the fixture's `home` path is never passed to `image_migration` or `image_gc`. Also add `test_startup_never_migrates`.
- [ ] **Step 2:** run them; they fail.
- [ ] **Step 3:** implement.
- [ ] **Step 4:** run them, plus `tests/test_image_surfaces.py tests/test_image_descriptions_store.py tests/test_image_subjects_store.py tests/test_image_collections.py tests/test_image_collection_imports.py tests/test_frozen_campaign.py`; they pass.
- [ ] **Step 5:** commit: `feat(images): migrate legacy images, verify, fold, then clean up`.

### Task 5: Conflicts in every describe queue

**Files:**
- Modify:
  - `image_descriptions.py`, `world_images.py`, `campaign_images.py`: rows carry `conflicts`; `set_in` clears them
  - `world_bundle.py` and `image_store.merge_projection`: conflicts count as described
  - `frontend/src/components/DescribeQueue.tsx`, `frontend/src/api/types.ts`
- Test: `backend/tests/test_image_descriptions_store.py`, `backend/tests/test_world_images_store.py`, `backend/tests/test_campaign_images_store.py`, `backend/tests/test_world_bundle.py`, `frontend/src/components/DescribeQueue.test.tsx`

- [ ] **Step 1: Write the failing tests.**
  - `test_a_conflicted_object_is_queued_with_its_texts_in_every_queue`.
  - `test_resolving_a_conflict_writes_the_description_and_clears_the_list`.
  - `test_bundle_fill_never_buries_a_conflict`.
  - Frontend: `offers each conflicting text as a choice`.
- [ ] **Step 2:** run them; they fail.
- [ ] **Step 3:** implement.
- [ ] **Step 4:** run them, plus `tsc` and `check-eslint`; they pass.
- [ ] **Step 5:** commit: `feat(images): every describe queue resolves migration conflicts`.

### Task 6: Garbage collection

**Files:**
- Create: `backend/src/grimoire/store/image_gc.py`
- Modify: `backend/src/grimoire/store/image_refs.py` (`walk_strict`, `ImageRefParseError`), `locks.py` (`OUTSIDE_DOMAIN`), `store/__init__.py`, `tests/store_api_baseline.json`
- Test: `backend/tests/test_image_gc.py`

**Interfaces:**
- `image_gc.scan(root, *, cancel) -> dict` returns a dry-run report with a token.
- `image_gc.collect(root, token: str, *, cancel) -> dict`.
- Constants: `GRACE_DAYS_DEFAULT = 30`, `GRACE_DAYS_MIN = 7`, `SCAN_GAP_HOURS = 24`, `BATCH_OBJECTS = 100`, `BATCH_SECONDS = 2.0`.

- [ ] **Step 1: Write the failing tests.**
  - `test_referenced_and_multiply_referenced_images_are_kept`.
  - Strict roots (Review Focus 2):
    - `test_a_sync_conflict_copy_of_a_placement_is_a_root`;
    - `test_an_unreadable_dir_an_unknown_format_and_a_symlink_abort`;
    - `test_manifests_journals_staging_and_pending_map_entries_are_roots`;
    - `test_an_old_staging_dir_is_walked_and_a_young_one_aborts`.
  - Grace, sightings and clock skew:
    - `test_recent_orphans_are_grace_protected`;
    - `test_collectable_only_after_grace_old_first_sighting_and_a_second_scan`;
    - `test_a_reachable_sighting_resets_the_clock`;
    - `test_future_mtimes_are_never_collectable`.
  - Blob refcounts:
    - `test_a_shared_blob_survives_its_unreachable_object`;
    - `test_an_unreadable_sidecar_keeps_every_blob`;
    - `test_an_orphan_blob_is_a_candidate_after_grace`.
  - Racing ingests: `test_a_concurrent_ingest_is_never_left_dangling`, which blocks GC inside a batch and ingests the same bytes from a thread.
  - The token:
    - `test_a_token_is_single_use_device_bound_and_expires`;
    - `test_collect_deletes_at_most_the_reported_ids`.
  - Root safety: `test_a_root_flip_mid_collect_deletes_nothing`, `test_nothing_outside_image_store_is_deleted`.
  - Batching: `test_deletion_is_batched_and_releases_the_ingest_lock`.
- [ ] **Step 2:** run them; they fail.
- [ ] **Step 3:** implement.
- [ ] **Step 4:** run the file plus the guards; it passes.
- [ ] **Step 5:** commit: `feat(images): a fail-closed, two-sighting garbage collector`.

### Task 7: Maintenance routes and the Settings card

**Files:**
- Create: `backend/src/grimoire/routes/maintenance.py` (`def` handlers). Each answers 202 with a run and requires `X-Grimoire-Attempt`.
  - `POST /maintenance/images/migrate` (body `{"dry_run": bool = true}`);
  - `POST /maintenance/images/gc` (body `{"dry_run": bool = true, "token": str | None}`);
  - `GET /maintenance/images/reports/{run_id}`.
- Modify: `backend/src/grimoire/routes/__init__.py`, `frontend/src/api/client.ts`, `frontend/src/api/types.ts`, `frontend/src/routes/ConfigView.tsx`
- Create: `frontend/src/components/ImageStoreCard.tsx`, `frontend/src/components/ImageStoreCard.test.tsx`
- Test: `backend/tests/test_maintenance_routes.py`, `backend/tests/test_route_order.py`

- [ ] **Step 1: Write the failing tests.**
  - Backend:
    - `test_missing_body_means_dry_run`;
    - `test_gc_delete_without_token_is_400`;
    - `test_second_start_is_409`;
    - `test_report_route_validates_the_run_id_and_404s_unknown`.
  - Frontend:
    - `check shows the dry-run report`;
    - `migrate asks to confirm first`;
    - `find unused images shows when objects become collectable`;
    - `delete carries the token`;
    - `a live run is rediscovered on mount`;
    - `cancel calls the global cancel route`;
    - `blocking paths and maintenance elsewhere are shown`.
- [ ] **Step 2:** run them; they fail.
- [ ] **Step 3:** implement.
- [ ] **Step 4:** run them, plus `tests/test_route_order.py`, `tsc` and `check-eslint`; they pass.
- [ ] **Step 5:** commit: `feat(images): Settings runs image migration and clean-up`.

### Task 8: Docs, CLAUDE.md, spec amendments, gate

**Files:**
- Modify:
  - **The spec:** add "### Stage-4 amendments", with M1–M17 one bullet each, and correct stage-3's C13 note on `.cache`.
  - **`docs/store-guarantees.md`:**
    - the `maintenance` class and its refusals;
    - the M5 lock order and striped families, in the lock tables and the contended-lock list;
    - what GC promises: grace, sightings, refcount, fail-closed, token;
    - replace "No collector exists yet" and "Nothing is collected yet".
  - **`CLAUDE.md`:**
    - "Detached runs": the class list and the handler count, recounted from the code;
    - the images note: migration and collection exist, and are started from Settings.
  - **Docstrings:** `assets.delete_in` and the legacy-entry note in `thumbs.py`.
- Test: `backend/tests/test_docs_guard.py`, in its style:
  - CLAUDE.md names every `runs.RunClass`;
  - `store-guarantees` names `image_name_lock`, `image_sidecar_lock` and `IMAGE_NAME_STRIPES`, and all three exist.

- [ ] **Step 1:** write the guard assertions; they fail.
- [ ] **Step 2:** write the docs; the guard passes.
- [ ] **Step 3:** full gate: `make check PY=backend/.venv/bin/python`.
- [ ] **Step 4:** commit: `docs(images): stage-4 amendments, guarantees and run classes`.
