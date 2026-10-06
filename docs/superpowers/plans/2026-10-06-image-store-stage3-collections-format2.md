# Content-Addressed Image Store — Stage 3 (Collections, Format 2) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** an image collection names its members by image id. New collections are written as format 2, `{"format": 2, "members": ["px1-…", …]}`, and their members are no longer world-library images.
- **Format-1 collections stay as they are.** Their manifests, URLs, subject keys and guard keep working through world-library names until stage 4 migrates them.
- **`guard_write` stops doing anything** in a world that has no format-1 state.

**Architecture:**
- **`image_collections`** reads both formats and writes only format 2.
  - `put_member` ingests a member and returns its id.
  - A format-2 member is served by its index: `/api/worlds/{wid}/image-collections/{id}/members/{n}`.
  - A format-1 collection keeps emitting today's library URLs.
- **Consumers learn the format-2 member keys:** greeting subjects and tiles, copy-from-greeting, export, image usage, world bundles (bundle format 3 when they carry a format-2 manifest), and the frontend collection viewer.
- **Order of work:** each task leaves every test green. Bundles learn format 2 before anything writes it.

**Tech Stack:** Python 3.11+, FastAPI, pytest; React + TypeScript, vitest.

**Spec:** `docs/superpowers/specs/2026-10-05-content-addressed-image-store-design.md`, stage 3 of §14. Read:
- §10 "Collections, format 2";
- §10 bundles;
- §12's GC roots;
- §13 "Collections, export, bundles, backups";
- every amendment section, including stage 2's.

**Precondition:** stage 2 (all of its tasks) is complete on this branch. Stage 2 Task 3's `greeting_images.catalog_with_slots` returns values `(entry, target)` with the tagged `target = ("slot", (dir, name), image_id | None) | None` (the id is carried from the listing so the todo hot path never re-reads placements). That was a controller ruling given to its implementer, and Task 4 verifies it. If `_placement_of` is untagged, Task 4 makes it target-aware. This stage adds `("object", image_id)`. A code map is at `.superpowers/sdd/stage3-notes/code-map.md`.

## Stage-3 rulings (the spec gains them in Task 6)

**C1. Format 2 is the only format written.**
- `publish(wid, id, members)` takes image ids and writes `{"format": 2, "members": [...]}`.
  - It refuses a member whose object does not resolve.
  - Published manifests are immutable: an existing manifest must be equal in format and members.
- `put_member(wid, data) -> str` validates exactly as today (`image_library.validate_size`, `covers.validate`), ingests, and returns the **image id**. It writes no world-library placement.

**C2. Both formats are read.**
- `read(wid, id)` returns `{"format": 1 | 2, "members": [...]}`.
  - The format is checked with `type(fmt) is int`, so JSON `true` is refused.
  - Format 1 keeps today's validation.
  - Format 2: every member matches `image_hash.ID_RE`; members are non-empty, at most 10000, and unique.
  - Anything else raises `CollectionInvalidError`.
- A format-2 member resolves through `image_refs.resolve_ref(image_refs.Ref(name="", image=id, focus=None))`. No new primitive is needed.

**C3. Member URLs per format.**
- **Format 1:** `available()` rows and the collection JSON keep **today's world-library URLs**, `/api/worlds/{wid}/images/{name}?v=<v>`. Subject keys, the to-do dedupe and export rules are therefore unchanged for every existing collection.
- **Format 2:**
  - Member `n` (0-based) is `/api/worlds/{wid}/image-collections/{id}/members/{n}?v=<blob sha>`.
  - `available()` skips a member that does not resolve, but never reuses its index.
- **Both formats:** the member route serves index `n` (for format 1 it serves that member's library file), so a direct member URL always works.
- **Route parameter:** the index is a `str` path parameter. It must match `^\d+$` and `int(n) < len(members)`; anything else answers 404.

**C4. `guard_write` is retired by condition.**
- `has_format1(wid) -> bool` is true when the world has either:
  - any format-1 manifest, or any manifest that does not read as valid (fail closed, as `referenced` does); or
  - any harvest journal under `.cache/image-collection-imports/<wid>/` that is still format 1.
- `guard_write` and `check_member_name` return immediately when `has_format1` is false.
- `referenced` considers format-1 manifests only.
- An unparseable journal counts as format-1 state (fail closed).
- Nothing in the app converts a journal except `sample()` and `accept()`, so a leftover or unmappable format-1 journal keeps the guard on in its world until stage 4 retires it (C13).
- Deleting the guard code waits for stage 4.

**C5. Harvest journals hold ids.**
- New journals are `format: 2`, with `members` as image ids. `accept` publishes format 2.
- **Converting a format-1 journal** happens when it is first read, under the job lock and then the collection lock:
  - A name maps to the id of its world-library placement.
  - For a legacy file, the bytes' `sha256` is checked against the name first (as format-1 `publish` does), then the file is ingested.
  - The converted journal keeps the original names as `legacy_members`, and is saved as format 2 immediately.
  - A name that maps to nothing refuses the journal, as today's corrupt-journal rule does.
  - **An accepted journal whose format-1 manifest is already published is not converted.** It reconciles against its names exactly as today (the manifest is authoritative), even if a member file is now missing.
- **`_reconcile_publication`** compares in the manifest's own format: `legacy_members` against a format-1 manifest, `members` against a format-2 one.
- Journal ids are GC roots (stage 4).

**C6. Greeting subjects for format-2 members.**
- A format-2 member's catalog key is its member URL without the query.
- **Same world:** when `canonical_id(<collection wid>) == canonical_id(root.name)`, `catalog_with_slots` gives it the target `("object", image_id)`, and `image_subjects` writes its subjects straight to the object (stage 2's R1/R2/R8, with no placement step).
- **Other world:** the key stays in the greeting's `subjects.json` (stage 2's R11).
- Format-1 member keys are unchanged.

**C7. Greeting tiles and copy for format-2 members.**
- `greeting_images.local_path` and a target-aware `local_target` resolve a member URL through `image_collections.member_path`.
- `routes/greetings._greeting_image_urls` gives a member row a `v` (the blob sha), a `thumb` and an `image_id`.
- `image_subjects.copy_to_character` links by the object id for an object target.

**C8. Export.**
- `export._COLLECTION_URL` also matches `/image-collections/{id}/members/{n}` (with an optional query).
- **Format 1:** the campaign-library rule (tombstones respected), applied to the first available member, or to member `n`.
- **Format 2:** the blob path of the first available member, or of member `n`.
- **World used:** always the campaign's world, as today.

**C9. Bundles.**
- **The format written:** the bundle is format 3 only when the export packs a format-2 manifest; otherwise it is written as format 2, so collection-free worlds stay importable by older builds.
- **Format constants:** `_READABLE` is `{1, 2, 3}`, and a module constant `MAX_FORMAT = 3` (the highest format this build reads) drives `_read_manifest`'s "newer than this grimoire" refusal.
- **Export validates manifests:** it runs the store's manifest validation over every manifest it packs, and refuses with `BundleError` naming the manifest before writing anything. An export therefore never writes a manifest its own import refuses.
- **Export:**
  - adds the ids named by format-2 manifests to the image entries;
  - checks their blobs in `_check_blob_sizes`.
- **Import of a format-3 bundle:** accepts manifest formats 1 and 2. `_check_collections` validates format-2 manifests with the store's validator and refuses the bundle on an invalid one. `_contain_manifests`:
  - finds manifests by folded path, as `_contain_refs` does;
  - rewrites members through `id_map`;
  - drops a member the bundle does not contain, logging it;
  - keeps the first of any duplicates the rewrite creates;
  - deletes a manifest left empty, logging it.
- **Import of a format-1 or format-2 bundle:** still refuses any manifest whose format is not 1.

**C10. Usage.** `image_usage.find`'s `collections` bucket lists format-2 members by id, and format-1 members by their library placement.

**C11. Accepted changes for format-2 members** (the spec and `store-guarantees.md` state them). A format-2 member:
- is not a world-library image, so it is absent from the library listing, the gallery, the world describe queue and art recall, and cannot be hidden per campaign;
- is tagged through greetings like any picture;
- is deduplicated by harvest by pixel id, not by bytes;
- makes an older grimoire sharing the store through a synced folder raise on that world's library writes. Upgrade every device (the stage-1 release note already says so).

**C12. Frontend.**
- `localMembers` checks the member shape **per format**:
  - format 1 keeps today's check (the library prefix plus 64 hex);
  - format 2 requires the prefix `/api/worlds/${world}/image-collections/${id}/members/`, compared as a string, followed by `\d+` and an optional `?v=…`.
- Anything else throws, and the viewer falls back to `/image`.
- The subject key for a member is its URL without the query.

**C13. Stage-4 notes** (recorded, not built):
- migration must keep member order and indices, including those of missing members, because index URLs are cached immutable;
- migration converts or retires leftover format-1 harvest journals (they hold the C4 guard on);
- journals live in `.cache`, which sync excludes, so one device's GC cannot see another device's in-flight journals.

## Global Constraints

- Stage 2's Global Constraints all hold: atomic writes, the `update` callback contract, imports, privacy, ratchets, the docs guard, and the frozen snapshot unchanged.
- **Lock order:** `image_collection_job_lock`, then `image_collection_lock`, then any image, object or ingest lock.
- **Test data:** collection ids are 32 hex (`0123456789abcdef0123456789abcdef`), and world names are invented.
- **Format-1 test state** is built only through `backend/tests/collection_fixtures.py: format1(wid, cid, *datas) -> list[str]`, created in Task 1. It places library images through `world_images.put_image` and hand-writes a `{"format": 1, ...}` manifest.
  - Existing format-1 tests move to it.
  - Format-2 tests are **added** beside them; none replaces them.
- **Route order:** the member route must not shadow `/image-collections/{id}/image` or be shadowed by it. `test_route_order.py` stays green.
- **How to run things:**
  - Backend tests from `backend/`: `PYTHONPATH=src .venv/bin/python -m pytest <path> -q`.
  - Frontend from `frontend/`: `npx vitest run <path>` and `npx tsc --noEmit -p .`.
  - Full gate: `make check`. The only allowed failure is the root-only `test_atomic` one.

## Review Focus

1. **Existing format-1 collections are untouched.** Their JSON, member URLs, subject keys, to-do dedupe, export and guard all behave exactly as before. A world holding both formats guards its format-1 names, and its format-2 manifest never makes a library write fail.
2. **Index stability.** When a format-2 member's blob is missing, it is skipped, every other URL keeps its index, and its own index answers 404, never another picture. `-1`, a huge index and `abc` all answer 404.
3. **Fail closed.** A corrupt or half-synced manifest, or an unconverted format-1 journal, keeps the guard active.
4. **Hostile or drifted bundles.**
   - a member id the bundle does not carry is dropped;
   - duplicates created by the id rewrite collapse;
   - a case-variant manifest path is contained;
   - an invalid format-2 manifest refuses the import.
5. **A format-1 journal in flight across the upgrade.** It converts, reconciles a publication that crashed before confirming, and never publishes a name that maps to the wrong picture.

---

### Task 1: Read both formats, the member route, the retired guard (writes still format 1)

**Files:**
- Create: `backend/tests/collection_fixtures.py`
- Modify:
  - `backend/src/grimoire/store/image_collections.py`: `read` per C2; `available` per C3; `member_path`, `member_url`, `has_format1`, `referenced`, and the `guard_write` / `check_member_name` condition (C4)
  - `backend/src/grimoire/routes/world_images.py`: the member route (C3), and the JSON shape per format
- Test: `backend/tests/test_image_collections.py`, `backend/tests/test_image_collection_routes.py`, `backend/tests/test_route_order.py`

**Interfaces:**
- **Produces in `image_collections`:**
  - `read(wid, id) -> dict`;
  - `available(wid, id) -> list[dict]`, rows `{index, path, url, image_id?}`;
  - `member_path(wid, id, index: int) -> Path | None`;
  - `member_url(wid, id, index: int, v: str) -> str`;
  - `has_format1(wid) -> bool`;
  - `referenced(wid, name) -> bool`;
  - `guard_write` and `check_member_name`.
- **Unchanged in this task:** `publish` and `put_member` still write format 1. Tests create format-2 state by hand-writing manifests over ingested objects.

- [ ] **Step 1: Write the failing tests.**
  - `test_read_accepts_both_formats_and_refuses_others`, parameterized over: format 3, `True`, `"2"`, a member that is not an id, duplicates, 10001 members.
  - `test_format_1_json_keeps_library_urls` (Review Focus 1).
  - `test_format_2_json_lists_member_index_urls_with_blob_v`.
  - `test_available_keeps_indices_stable_when_a_member_is_missing` (Review Focus 2).
  - `test_member_route_serves_both_formats`.
  - `test_member_route_bounds`: `-1`, `10**9`, `abc`, past the end, and a missing member all answer 404.
  - `test_format_2_manifests_never_guard_library_names`.
  - `test_guard_is_inert_only_without_format_1_state`. In a format-2-only world, a member-shaped name with mismatched bytes is **accepted** by `put_image`. The same PUT is refused (400) once a format-1 manifest, a format-1 journal, or a corrupt manifest exists (Review Focus 3).
  - `test_format_1_members_stay_guarded_beside_format_2`.
- [ ] **Step 2:** run them; they fail.
- [ ] **Step 3:** implement. Move existing format-1 test setup onto `collection_fixtures.format1`.
- [ ] **Step 4:** run both files, plus `tests/test_world_images_store.py tests/test_world_images_routes.py tests/test_image_surfaces.py tests/test_todo_route.py tests/test_route_order.py`; everything passes.
- [ ] **Step 5:** commit: `feat(images): collections read format 2 and serve members by index`.

### Task 2: World bundles carry format-2 collections (bundle format 3)

**Files:**
- Modify: `backend/src/grimoire/store/world_bundle.py`: the chosen `FORMAT`, `_READABLE`, the per-bundle-format manifest acceptance, `_manifest_ids`, `_image_entries`, `_check_blob_sizes`, `_check_collections` (format-2 validation), and a new `_contain_manifests`
- Test: `backend/tests/test_world_bundle.py`, `backend/tests/test_image_collection_routes.py` (the fork and bundle round trip)

**Interfaces:**
- **Produces:**
  - `_manifest_ids(root) -> set[str]`;
  - `_contain_manifests(staging, id_map, contained) -> tuple[int, int]`: members dropped, and manifests removed.

- [ ] **Step 1: Write the failing tests.**
  - `test_a_collection_free_world_still_writes_bundle_format_2`.
  - `test_a_format_2_collection_round_trips_into_a_fresh_store`: delete the world and wipe the image store, then serve every member through the member route.
  - `test_import_rewrites_format_2_members_to_local_ids`.
  - `test_rewrite_duplicates_collapse_to_the_first`.
  - `test_a_member_the_bundle_does_not_carry_is_dropped` (Review Focus 4).
  - `test_a_manifest_emptied_by_containment_is_removed`.
  - `test_a_case_variant_manifest_path_is_contained`.
  - `test_an_invalid_format_2_manifest_refuses_the_import`.
  - `test_export_refuses_an_oversized_member_blob`.
  - `test_export_refuses_an_invalid_format_2_manifest`.
  - `test_the_newer_than_this_grimoire_message_names_max_format`.
  - `test_bundle_format_2_still_refuses_a_format_2_manifest`.
  - Update the parameters of the existing newer- and unknown-format tests.
- [ ] **Step 2:** run them; they fail.
- [ ] **Step 3:** implement.
- [ ] **Step 4:** run `tests/test_world_bundle.py tests/test_image_collection_routes.py tests/test_world_fork.py`; they pass.
- [ ] **Step 5:** commit: `feat(images): world bundles carry format-2 collections`.

### Task 3: Write format 2 (`publish`, `put_member`, harvest journals)

**Files:**
- Modify: `backend/src/grimoire/store/image_collections.py` (`publish`, `put_member`), `backend/src/grimoire/store/image_collection_imports.py` (C5)
- Test: `backend/tests/test_image_collections.py`, `backend/tests/test_image_collection_imports.py`, `backend/tests/test_image_surfaces.py`

**Interfaces:**
- **Produces:**
  - `publish(wid, id, members: list[str]) -> dict`, ids in and format 2 out;
  - `put_member(wid, data) -> str`, returning the image id;
  - format-2 journals, with `legacy_members` on converted ones.

- [ ] **Step 1: Write the failing tests.**
  - `test_publish_writes_format_2_with_ids`.
  - `test_put_member_returns_an_id_and_places_nothing`.
  - `test_a_published_manifest_is_immutable_across_formats`.
  - `test_a_format_1_journal_converts_and_resumes_as_format_2`.
  - `test_a_format_1_journal_with_a_mismatched_legacy_file_refuses`.
  - `test_a_crashed_format_1_publication_reconciles_after_the_upgrade` (Review Focus 5).
  - `test_an_unmappable_format_1_journal_refuses`.
  - `test_an_accepted_format_1_journal_with_a_missing_member_still_reconciles`.
  - `test_an_unparseable_journal_keeps_the_guard_on`.
  - In `test_image_surfaces.py`: keep the format-1 `collection-member` surface (built on `collection_fixtures.format1`), and add `test_a_format_2_member_writes_no_library_file_or_placement` outside `SURFACES`.
  - `test_a_format_2_member_is_not_in_the_describe_queue` (C11).
- [ ] **Step 2:** run them; they fail.
- [ ] **Step 3:** implement.
- [ ] **Step 4:** run the three files, plus `tests/test_world_images_routes.py tests/test_image_collection_routes.py tests/test_world_bundle.py`; they pass.
- [ ] **Step 5:** commit: `feat(images): new collections are written as format 2`.

### Task 4: Greeting subjects, tiles, copy, export and usage for format-2 members

**Files:**
- Modify:
  - `backend/src/grimoire/store/greeting_images.py`: `_collection_members`, `local_path`, `local_target`, and the `catalog_with_slots` object targets (C6, C7)
  - `backend/src/grimoire/store/image_subjects.py`: object targets, `copy_to_character`
  - `backend/src/grimoire/routes/greetings.py`: `_greeting_image_urls`
  - `backend/src/grimoire/store/export.py`: C8
  - `backend/src/grimoire/store/image_usage.py`: C10
- Test:
  - `backend/tests/test_image_subjects_store.py`
  - `backend/tests/test_image_usage.py`
  - `backend/tests/test_todo_route.py`
  - `backend/tests/test_image_collection_routes.py` (export)
  - the greeting route tests

- [ ] **Step 1: Write the failing tests.**
  - `test_tagging_a_format_2_member_writes_its_object`.
  - `test_a_cross_world_format_2_member_tag_stays_in_the_sidecar`.
  - `test_a_format_2_member_tag_survives_fork_world`.
  - `test_untagged_lists_format_2_members_once_each`: a new format-2 copy of `test_todo_route.py:121`; the format-1 test stays.
  - `test_a_format_2_member_tile_has_v_thumb_and_image_id`.
  - `test_copy_from_greeting_links_a_format_2_member` (200).
  - `test_export_resolves_a_format_2_collection_and_a_direct_member_url`.
  - `test_export_resolves_a_direct_format_1_member_url_with_the_campaign_rule`.
  - `test_export_keeps_campaign_tombstones_for_format_1`.
  - `test_export_resolves_through_the_campaigns_world`.
  - `test_usage_lists_a_format_2_collection_member`.
- [ ] **Step 2:** run them; they fail.
- [ ] **Step 3:** implement.
- [ ] **Step 4:** run the files, plus `tests/test_world_gallery_route.py tests/test_post_images.py`; they pass.
- [ ] **Step 5:** commit: `feat(images): format-2 collection members are tagged, shown, copied and exported by id`.

### Task 5: Frontend collection viewer and subject keys

**Files:**
- Modify: `frontend/src/markdown/MarkdownImage.tsx` (`localMembers`, C12)
- Test: `frontend/src/markdown/MarkdownImage.test.tsx`, `frontend/src/components/GreetingEditor.test.tsx`

- [ ] **Step 1: Write the failing tests.** Add these; keep every existing format-1 test.
  - `accepts a format 2 collection of member URLs`.
  - `refuses a format 2 member URL from another world or collection`.
  - `refuses a format 1 collection carrying member URLs`.
  - `refuses an unknown format`.
  - In `GreetingEditor.test.tsx`: `format 2 collection members expose subject controls keyed by member URL`.
- [ ] **Step 2:** run them; they fail.
- [ ] **Step 3:** implement.
- [ ] **Step 4:** run them, plus `npx tsc --noEmit -p .` and `make check-eslint`; they pass.
- [ ] **Step 5:** commit: `feat(images): the collection viewer reads format-2 member URLs`.

### Task 6: Docs, spec amendments, gate

**Files:**
- Modify:
  - **The spec:** add a "### Stage-3 amendments" section with C1–C13, one bullet each, and correct the stale §12 line ("a collection member upload refused by its guard after ingest": the guard uses `identify`).
  - **`docs/store-guarantees.md`:**
    - `image_collection_lock` and `image_collection_job_lock` in the lock tables, with their order;
    - collection members by id;
    - `guard_write`'s retirement condition;
    - C11.
  - The module docstrings of `image_collections` and `image_collection_imports`.
- Test: `backend/tests/test_docs_guard.py`. In its style, the image section names both collection locks, and both exist in `locks.py`.

- [ ] **Step 1:** write the docs-guard assertion; it fails.
- [ ] **Step 2:** write the docs; it passes.
- [ ] **Step 3:** run the full gate, `make check PY=backend/.venv/bin/python`. Only the allowed failure is acceptable, ratchets stay at baseline, and the frozen snapshot is unchanged.
- [ ] **Step 4:** commit: `docs(images): stage-3 amendments and guarantees`.
