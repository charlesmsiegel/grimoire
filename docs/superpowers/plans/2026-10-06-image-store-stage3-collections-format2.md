# Content-Addressed Image Store — Stage 3 (Collections, Format 2) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** An image collection names its members by image id. New collections are written as format 2, `{"format": 2, "members": ["px1-…", …]}`, so members no longer have to be world-library images.

**How:**
- Each member is served by its index in the collection.
- Format-1 manifests keep working through their world-library names until stage 4 migrates them.
- `guard_write`, which protects world-library names that format-1 manifests point at, becomes inert in any world that has no format-1 manifest.

**Architecture:**
- **`image_collections`** reads both formats and writes only format 2.
  - `put_member` ingests a member and returns its id; it no longer places anything in the world library.
- **Routing:**
  - A new member route serves `/api/worlds/{wid}/image-collections/{id}/members/{n}`.
  - The collection JSON lists those URLs (each with `?v=<blob sha>`) for a format-2 manifest, and today's library URLs for a format-1 manifest.
- **Consumers learn the new member keys:** greeting subjects, export, image usage, world bundles (bundle format 3) and the frontend's collection viewer.

**Tech Stack:** Python 3.11+, FastAPI, pytest; React + TypeScript, vitest.

**Spec:** `docs/superpowers/specs/2026-10-05-content-addressed-image-store-design.md`, stage 3 of §14. Read:
- §10 "Collections, format 2";
- §10 bundles;
- §12's GC roots;
- §13 "Collections, export, bundles, backups";
- the amendment sections at the end, including stage 2's.

A code map is at `.superpowers/sdd/stage3-notes/code-map.md` (git-ignored; the controller hands it to implementers).

## Stage-3 rulings (the spec gains them in Task 6)

**C1. Format 2 is the only format written.**
- `publish(wid, collection_id, members)` takes image ids and writes `{"format": 2, "members": [...]}`. It refuses a member whose object does not resolve.
- Published manifests stay immutable: an existing manifest must be equal in **both** format and members.
- `put_member(wid, data) -> str` validates exactly as today (`image_library.validate_size`, `covers.validate`), ingests, and returns the **image id**. It writes no world-library placement.

**C2. Both formats are read.**
- `read(wid, id)` returns `{"format": 1|2, "members": [...]}`, validated per format.
  - Format 1: today's rules.
  - Format 2: every member matches `image_hash.ID_RE`; members are non-empty, at most 10000, and unique.
- Any other format raises `CollectionInvalidError`.

**C3. Member addressing.**
- Member `n` (0-based) of either format is served at `/api/worlds/{wid}/image-collections/{id}/members/{n}`.
  - Format 2: the member's object resolves to its blob.
  - Format 1: the member name resolves through the world library (`assets.path_in(..., supported_only=True)`).
- `available(wid, id)` returns rows `{"index", "path", "url", "image_id"?}` for members that resolve, in manifest order.
  - The url is `/api/worlds/{wid}/image-collections/{id}/members/{index}?v=<v>`, where `v` is the blob sha (or the legacy stat token for a format-1 legacy file).
  - A missing member is skipped, and its index is never reused, so URLs stay stable.
- `GET /api/worlds/{wid}/image-collections/{id}` returns `{"format", "id", "members": [url, …]}`, using those index URLs for **both** formats, so the client sees one shape.

**C4. `guard_write` is retired by condition.**
- `referenced(wid, name)` considers **format-1 manifests only**. A format-2 manifest never refers to a library name, so it can never make a library write raise.
- `guard_write` and `check_member_name` return immediately in a world with no format-1 manifest.
- Deleting the code waits for stage 4, once migration has rewritten every format-1 manifest.

**C5. Harvest journals hold ids.**
- New journals are `format: 2` with `members` as image ids. `accept` publishes format 2.
- A format-1 journal is converted on load:
  - a member name maps to the id of its world-library placement;
  - a legacy file is ingested to get its id.
- A name that maps to nothing makes the journal unresumable, exactly like today's corrupt-journal refusal.
- Journal ids are GC roots (stage 4).

**C6. Greeting subjects for format-2 members.**
- A format-2 member's catalog key is its member URL without the query, `/api/worlds/{wid}/image-collections/{id}/members/{n}`.
- `catalog_with_slots` gives it an **object target** (the image id) instead of a slot.
- `image_subjects` writes subjects for an object target straight to the object, under the world scope (R1/R2/R8 of stage 2, with no placement step).
- Format-1 member keys are unchanged.

**C7. Export.** `export.resolve_url`'s collection branch:
- returns the first available member's path;
- for format 1, keeps today's campaign-library rule (tombstones respected);
- for format 2, returns the blob path directly.

**C8. Bundles move to format 3.**
- `FORMAT = 3` and `_READABLE = {1, 2, 3}`.
- **Export:** adds every id named by a format-2 manifest to the image entries, alongside the placement ids.
- **Import, bundle format 3:** accepts manifest formats 1 and 2.
  - A format-2 manifest's members are rewritten through `id_map` (recomputed local ids).
  - A member the bundle does not contain is dropped and logged, as `_contain_refs` does for refs.
  - A manifest left with no members is deleted from staging and logged.
- **Import, bundle formats 1 and 2:** still refuse a manifest whose format is not 1.

**C9. Usage.** `image_usage.find`'s `collections` bucket lists `{"wid", "collection"}` for format-2 members by id, as well as format-1 members by library placement.

**C10. Frontend.**
- `MarkdownImage`'s `localMembers` accepts `format` 1 or 2.
- Every member URL must be same-origin and match `^/api/worlds/${world}/image-collections/${id}/members/\d+(\?v=[0-9a-zA-Z-]+)?$`. The library-URL shape is no longer emitted by the server for either format.
- The subject key for a member is the URL without its query.

## Global Constraints

- Everything in stage 2's Global Constraints still holds: atomic writes, the `update` callback contract, imports, privacy, ratchets, the docs guard, and the frozen snapshot unchanged.
- **Locks:** `image_collection_job_lock` is taken before `image_collection_lock`, which is taken before any image or object lock. This is unchanged.
- **Placeholder names:** collection ids are 32 hex. Tests use `0123456789abcdef0123456789abcdef` and invented world names.
- **Route order:** the member route must not shadow, or be shadowed by, `/image-collections/{id}/image`. `test_route_order.py` stays green.
- **How to run things:** backend tests from `backend/` with `PYTHONPATH=src .venv/bin/python -m pytest <path> -q`; frontend from `frontend/` with `npx vitest run <path>` and `npx tsc --noEmit -p .`.

## Review Focus

1. **Collection JSON:** a format-1 manifest in a world that also has format-2 manifests. The format-1 members still serve, their library names stay guarded, and the format-2 manifest never makes a library PUT 500 (C4).
2. **Index URLs never shift:** a member whose blob is missing (mid-sync) is skipped. Every other member's URL keeps its index, and requesting the missing index gives 404, not another picture.
3. **A collection URL that names another world in a campaign's prose:** export still resolves through the campaign's world, as today (C7).
4. **A hostile bundle with a format-2 manifest naming an id it does not carry:** the member is dropped, never resolved against a local object (C8).
5. **A format-1 harvest journal in progress when the upgrade lands:** it resumes as format 2, or refuses cleanly. It never publishes a mixed manifest (C5).

---

### Task 1: Manifest format 2 in `image_collections` and harvest journals

**Files:**
- Modify: `backend/src/grimoire/store/image_collections.py`, `backend/src/grimoire/store/image_collection_imports.py`
- Test: `backend/tests/test_image_collections.py`, `backend/tests/test_image_collection_imports.py`

**Interfaces:**
- **Produces in `image_collections`:**
  - `read(wid, id) -> dict` (C2);
  - `publish(wid, id, members: list[str]) -> dict`, ids in and format 2 out (C1);
  - `put_member(wid, data) -> str`, returning the image id (C1);
  - `available(wid, id) -> list[dict]`, rows `{index, path, url, image_id?}` (C3);
  - `member_path(wid, id, index: int) -> Path | None`, the path a member URL serves;
  - `referenced` (format-1 only, C4);
  - `guard_write` and `check_member_name` (inert without format-1, C4);
  - `has_format1(wid) -> bool`;
  - `member_url(wid, id, index, v) -> str`.
- **Produces in `image_collection_imports`:** journals in format 2 (C5), with `_read` converting format 1.

- [ ] **Step 1: Write the failing tests.**
  - `test_publish_writes_format_2_with_ids`.
  - `test_put_member_returns_an_id_and_places_nothing`: `image_store.read(id)` is set, and the world library listing is unchanged.
  - `test_read_accepts_both_formats_and_refuses_others` (parameterized).
  - `test_available_keeps_indices_stable_when_a_member_is_missing` (Review Focus 2).
  - `test_format_2_manifests_never_guard_library_names`.
  - `test_guard_is_inert_without_format_1` (C4): with a monkeypatched `assets.path_in` spy, a world with only format-2 manifests makes no library reads in `guard_write`.
  - `test_format_1_members_stay_guarded_beside_format_2` (Review Focus 1).
  - `test_a_published_manifest_is_immutable_across_formats`.
  - `test_a_format_1_journal_resumes_as_format_2` and `test_an_unmappable_format_1_journal_refuses` (Review Focus 5).
- [ ] **Step 2:** run them; they fail.
- [ ] **Step 3:** implement. Existing format-1 tests that asserted `put_member` places a library file are rewritten to the C1 contract, without weakening any guard assertion for format-1 worlds.
- [ ] **Step 4:** run both files plus `tests/test_world_images_store.py tests/test_world_images_routes.py tests/test_image_surfaces.py`; they pass. In the `collection-member` surface of `test_image_surfaces.py`, the member is now an object, so assert that no library file and no library placement is written.
- [ ] **Step 5:** commit: `feat(images): collection manifests name members by image id (format 2)`.

### Task 2: Member route, collection JSON, export

**Files:**
- Modify:
  - `backend/src/grimoire/routes/world_images.py`: the member route, the JSON shape (C3), and the `/image` fallback serving member 0 of `available`
  - `backend/src/grimoire/store/export.py`: the collection branch of `resolve_url` (C7)
- Test: `backend/tests/test_image_collection_routes.py`, `backend/tests/test_export_store.py` (or wherever `resolve_url` is tested), `backend/tests/test_route_order.py`

**Interfaces:**
- **Consumes:** Task 1's `available`, `member_path`, `read`.
- **Produces:**
  - `GET /api/worlds/{wid}/image-collections/{collection_id}/members/{index}` serves through `_serve_image_file`. A `?v=` naming the blob is immutable, as for any blob (stage-1 rule).
  - 404 for an absent manifest, a malformed id, an index out of range, or a member that does not resolve.
  - 500 only for `CollectionInvalidError`, as today.

- [ ] **Step 1: Write the failing tests.**
  - `test_member_route_serves_each_index_with_a_blob_etag`.
  - `test_missing_member_index_is_404_not_another_picture` (Review Focus 2).
  - `test_collection_json_lists_member_urls_for_both_formats`.
  - `test_fallback_serves_the_first_available_member`.
  - `test_export_resolves_a_format_2_collection_to_a_blob`.
  - `test_export_keeps_campaign_tombstones_for_format_1`.
  - `test_export_resolves_through_the_campaigns_world` (Review Focus 3).
- [ ] **Step 2:** run them; they fail.
- [ ] **Step 3:** implement.
- [ ] **Step 4:** run the files plus `tests/test_route_order.py tests/test_post_images.py`; they pass.
- [ ] **Step 5:** commit: `feat(images): collection members are served by index`.

### Task 3: Subjects, usage and the to-do list for format-2 members

**Files:**
- Modify:
  - `backend/src/grimoire/store/greeting_images.py`: `_collection_members` returns member URLs from `available`, with `catalog_with_slots` giving an object target per C6
  - `backend/src/grimoire/store/image_subjects.py`: object targets
  - `backend/src/grimoire/store/image_usage.py`: the `collections` bucket for format 2 (C9)
- Test: `backend/tests/test_image_subjects_store.py`, `backend/tests/test_image_usage.py`, `backend/tests/test_todo_route.py`

**Interfaces:**
- **Consumes:**
  - stage 2 Task 3's `_placement_of`, `_scope`, and the R1/R2/R8 helpers;
  - stage 2 Task 5's `find`.
- **Produces:**
  - `catalog_with_slots` values are `(entry, target)`, where `target` is `("slot", (dir, name))`, `("object", image_id)` or None.
  - `catalog`'s public shape is unchanged (no target leaks).

- [ ] **Step 1: Write the failing tests.**
  - `test_tagging_a_format_2_member_writes_its_object`.
  - `test_a_format_2_member_tag_is_absent_in_another_world`.
  - `test_untagged_lists_format_2_members_once_each` (the dedupe test in `test_todo_route.py:121` is adapted to format 2).
  - `test_usage_lists_a_format_2_collection_member`.
- [ ] **Step 2:** run them; they fail.
- [ ] **Step 3:** implement.
- [ ] **Step 4:** run the files plus `tests/test_world_gallery_route.py`; they pass.
- [ ] **Step 5:** commit: `feat(images): format-2 collection members are tagged and counted by id`.

### Task 4: World bundles, format 3

**Files:**
- Modify: `backend/src/grimoire/store/world_bundle.py` (`FORMAT`, `_READABLE`, `_COLLECTION_FORMAT` → per-bundle-format acceptance, `_image_entries`, `_check_collections`, a new `_contain_manifests`)
- Test: `backend/tests/test_world_bundle.py`, `backend/tests/test_image_collection_routes.py` (the fork and bundle round trip)

**Interfaces:**
- **Produces:**
  - `_manifest_ids(root) -> set[str]` (format-2 member ids in a world tree).
  - `_contain_manifests(staging, id_map, contained) -> tuple[int, int]` (members dropped, manifests removed), logged through `logs.record` like `_contain_refs`.

- [ ] **Step 1: Write the failing tests.**
  - `test_a_format_2_collection_round_trips_into_a_fresh_store`: delete the world and wipe the image store before import, then serve every member.
  - `test_import_rewrites_format_2_members_to_local_ids` (decoder drift simulated as in the existing ref-rewrite test).
  - `test_a_format_2_member_the_bundle_does_not_carry_is_dropped` (Review Focus 4).
  - `test_a_manifest_emptied_by_containment_is_removed`.
  - `test_bundle_format_2_still_refuses_a_format_2_manifest`.
  - Update `test_a_newer_collection_manifest_refuses_the_import`'s parameters: format 3 bundles accept manifest format 2; manifest format 3 is refused.
- [ ] **Step 2:** run them; they fail.
- [ ] **Step 3:** implement.
- [ ] **Step 4:** run `tests/test_world_bundle.py tests/test_image_collection_routes.py tests/test_world_fork.py`; they pass.
- [ ] **Step 5:** commit: `feat(images): world bundles carry format-2 collections (bundle format 3)`.

### Task 5: Frontend collection viewer and subject keys

**Files:**
- Modify: `frontend/src/markdown/MarkdownImage.tsx` (`localMembers`, C10)
- Test: `frontend/src/markdown/MarkdownImage.test.tsx`, `frontend/src/components/GreetingEditor.test.tsx`

**Interfaces:**
- **Consumes:** Task 2's JSON shape.
- **Produces:** `localMembers(raw, world, id)` accepts formats 1 and 2 and member URLs of the C10 shape. Anything else throws, and the viewer falls back to `/image`, as today.

- [ ] **Step 1: Write the failing tests.**
  - In `MarkdownImage.test.tsx`:
    - `accepts a format 2 collection of member URLs`;
    - `refuses a member URL from another world or collection`;
    - `refuses an unknown format`;
    - update the existing fixtures to member URLs.
  - In `GreetingEditor.test.tsx`, adapt `collection members expose subject controls for the selected picture` to member URLs.
- [ ] **Step 2:** run them; they fail.
- [ ] **Step 3:** implement.
- [ ] **Step 4:** run them plus `npx tsc --noEmit -p .` and `make check-eslint`; they pass.
- [ ] **Step 5:** commit: `feat(images): the collection viewer reads member URLs`.

### Task 6: Docs, spec amendments, gate

**Files:**
- Modify:
  - **The spec:**
    - add a "### Stage-3 amendments" section with C1–C10, one bullet each;
    - correct the stale §12 line about a collection guard ingesting before refusing (the guard uses `identify`).
  - **`docs/store-guarantees.md`:**
    - add `image_collection_lock` and `image_collection_job_lock` to the lock tables, with their order;
    - collection members by id;
    - `guard_write`'s retirement condition.
  - The module docstrings of `image_collections` and `image_collection_imports`.
- Test: `backend/tests/test_docs_guard.py`. In its style, require the image section to name both collection locks, and require that they exist in `locks.py`.

- [ ] **Step 1:** write the docs-guard assertion; it fails.
- [ ] **Step 2:** write the docs; it passes.
- [ ] **Step 3:** run the full gate, `make check PY=backend/.venv/bin/python`. Only the allowed root-only failure is acceptable, ratchets stay at baseline, and the frozen snapshot is unchanged.
- [ ] **Step 4:** commit: `docs(images): stage-3 amendments and guarantees`.
