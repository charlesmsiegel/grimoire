# Content-Addressed Image Store — Stage 2 (Shared Metadata) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Facts about a picture live once, on its image object.
- The **description** is global: one per picture, the same in every world.
- **Subject tags** are scoped to one world or campaign.
- **Where a picture is used** is derived on demand and shown in a "Used in" panel.

Legacy sidecar entries keep showing exactly what they show today until stage 4 migrates them.

**Architecture:**
- **No new store of truth.** Descriptions and greeting subjects are read and written through the existing façades (`image_descriptions`, `image_subjects`, `overlay`, `campaign_images`, `world_images`). Each one routes a placement-backed name to its object through `image_store.update`, and keeps legacy names on their sidecars.
- **`image_scopes`** (new) strips a world's or campaign's scope from every object when it is deleted, and copies it when it is forked.
- **`image_usage`** (new) derives where an image is used by walking placements. A new `/api/images` router serves it, and a small frontend panel shows it.
- **`context.art`** dedupes its candidates by `image_id`.

**Tech Stack:** Python 3.11+, FastAPI, pytest; React + TypeScript, vitest.

**Spec:** `docs/superpowers/specs/2026-10-05-content-addressed-image-store-design.md`. This is stage 2 of §14. Read §3, §4, §6, §9, §13 ("Metadata"). Task 8 adds the rulings below to the spec as a "Stage-2 amendments" section.

## Stage-2 rulings

R1–R7 were decided with the user. R8–R13 come from the plan gate.

**R1. Precedence: the local legacy entry wins until migration.**
- For one directory `d` and an image-bearing placement `name`, the text is:
  1. `descriptions.json[name]` in `d`, if that key holds a string;
  2. otherwise the object's `description`, if it is a string;
  3. otherwise the image is undescribed.

  A legacy name with no placement reads only its key.
- Subjects follow the same rule:
  1. the `subjects.json[key]` list wins when present;
  2. otherwise, when the scope is in the object's `reviews.subjects`, the subjects are the `id`s of its associations that have `kind == "character"`, `relation == "subject"` and `scope == <scope>`;
  3. otherwise the image is unreviewed.

**R2. A write goes to the object behind the visible placement.**
- **The placement resolves, and `image_store.update` confirms the write (R8).**
  1. Set the field on the object.
  2. Delete the name's legacy key in (a) the directory being edited and (b) the visible placement's directory, if that is a different one.

  The edit then shows in both places, while other directories' keys keep masking it until migration.
- **The write is not confirmed, or the placement has not arrived.**
  - The write goes to the legacy key of the edited directory, exactly as today, and R1 shows it. No text is ever lost.
  - An unarrived placement can only be described when a legacy file of the same name sits beside it (the `_drop_if_placed` state). Without one, the name is not in `list_in`, and the name check refuses it, as today.
- **The name has no placement:** legacy, as today.
- **Order:** the object first, then the key deletions.

**R3. D1 for inherited art in campaigns.**
- A campaign edit of an image it inherits writes the shared object (R2). This covers records and the library alike.
- The campaign library route still answers 409 ("describe this image in its world"), but only for an inherited library image that the world holds as a **legacy file**. The route decides this itself, before the store is called.

**R4. Descriptions are global.** One picture has one description in every world. Subject associations stay scoped.

**R5. Promotion carry-up.**
- `overlay.promote_image` carries a text into the campaign only when the campaign has no string key for the name and the world directory's raw key for it is a string. That is the only case where the campaign would lose visible text after the promote.
- It carries that text as a campaign **legacy key** (`image_descriptions.carry_legacy`). A promote never touches the shared object.

**R6. "Used in".**
- The route is `GET /api/images/{image_id}/usage`.
- The UI is a collapsed "Used in…" control beside every `ImageDescriptionField` that has an `image_id`. It fetches only when opened.

**R7. Lock-domain guard.** `test_lock_domain_guard.py` treats `image_store.update` and `image_store.merge_projection` as writes. That way `image_scopes` is surveyed and can sit in `DOMAIN_MODULES`.

**R8. `image_store.update` returns `bool`.** It is `True` only when it wrote. A caller deletes a legacy key only after `True`. On `False`, it falls back to R2's legacy path.

**R9. One description cap, `image_store.MAX_DESCRIPTION = 4000`.**
- `world_bundle.MAX_IMPORTED_DESCRIPTION` becomes an alias of it.
- `image_descriptions.set_in` refuses longer text with `image_descriptions.DescriptionTooLongError(ValueError)`.
- Every description PUT route catches that exception **before** its existing `ValueError` → 404 handler and answers 422.
- Existing longer legacy keys still read.

**R10. Scope ids are canonical.**
- `world:<worlds.paths.canonical_id(wid)>`.
- `campaign:<cid>` with the cid as stored.

**R11. Cross-world references keep their tags in the sidecar.** A greeting's reference to a picture placed in another world keeps its tag in that greeting's `subjects.json`, so the tag travels with the greeting's world bundle.

**R12. A replaced picture sheds a stale caption.**
- **When the key is dropped:** `assets.put_in` or `link_in` replaces a slot that held a **known, different** identity, and the new object already has a string `description`. The name's legacy description key is dropped in the same operation. (The Chub replace already drops it.)
- **What counts as a known, different identity:**
  - the old placement's `image` is set and differs from the new one; or
  - the old slot is a legacy file whose `image_store.identify` differs, which is the test `characters._legacy_identity` already makes.
- **Never dropped:**
  - on a first placement (no prior identity);
  - on an adoption of the same picture;
  - on a promote's `link_in`. R5 carries the key just before it, and the slot had no placement.
- Otherwise today's rule stands: the old text stays.

**R13. Documented, not changed:**
- A global description now travels in every world's bundle that holds the picture, including text written in another world.
- `delete_campaign` now takes the campaign lock, so it can be refused (409) behind a long-running hold.
- `staging.repoint_urls` no longer rewrites a world URL written inside an object description.
- `delete_world` is unlocked, so a tag written between the strip and the `rmtree` survives. This is rare, and re-running the delete clears it.

## Global Constraints

- **Privacy:** invented names only (Seraphine, Mara, Winifred, Realm, Saltmarch). Never describe a real store's contents in a commit, comment, fixture or doc.
- **Writes:**
  - Every file write goes through `store.atomic`.
  - Every object write goes through `image_store.update`, whose callback is a pure dict edit: no store calls, and no campaign or ingest/GC lock (the latch enforces this). Anything a lock or the store is needed for is done before `update` is called.
  - No new `Path.home()`.
- **Imports:**
  - Module scope only, and the graph stays acyclic.
  - `image_store` and `image_refs` import nothing above them.
  - `image_descriptions` may not import `characters`, `overlay`, `campaign_images` or `world_images`.
  - `image_scopes` imports only `image_store`, `locks` and `worlds.paths`.
  - `image_usage` may import `image_store`, `image_refs`, `image_hash`, `paths`, `assets` (its path builders), `entities` (`ENTITY_KINDS`), `image_collections` (`directory`, `read`), and the world/campaign library and cover path helpers.
  - Bind submodules across packages.
- **Scopes and associations:**
  - Scope strings follow R10.
  - An association is `{"kind": "character", "relation": "subject", "scope": <scope>, "id": <cid>}`, all values strings.
  - `reviews` is `{"subjects": [<scope>, ...]}`, sorted and deduplicated.
- **Every new store module** goes in `store/__init__.py`'s import list and in the `"dir"` list of `backend/tests/store_api_baseline.json` (not `"all"`).
- **The frozen campaign's `snapshot.json` must not change.** Its store is legacy-only, and R1 reads it as today.
- **Test fixtures that share bytes now share an object.** Many store tests upload the same opaque `b"png"` for different images. Since R4, those images share one description. Where a test needs distinct texts, give the images distinct bytes (`b"png-1"`, `b"png-2"`, …). Never weaken an assertion.
- **Hot paths:**
  - `/api/shell` (every navigation), `/api/todo`, the per-turn art catalogue and the per-turn `overlay.read_descriptions` must not resolve blobs, and must not re-read placements that the caller's listing already read.
  - Object reads in whole-store sweeps (`image_scopes`, `image_usage`) use an unmemoized read (`image_store.read_fresh`, Task 1).
  - Every other object read goes through `image_store.read`, which memoizes in its **own statcache pool** (`pool="image_objects"`), so backlog walks never evict the shared pool's card and entity entries.
- **Ratchets** (ruff, mypy, eslint) stay at baseline; if one improves, run `make baseline` and commit the result.
- **The docs guard** stays green.
- **How to run things:**
  - Backend tests from `backend/`: `PYTHONPATH=src .venv/bin/python -m pytest <path> -q`.
  - Frontend from `frontend/`: `npx vitest run <path>` and `npx tsc --noEmit -p .`.
  - Full gate: `make check`. The only allowed failure is the root-only `test_atomic.py::test_a_read_only_record_is_not_silently_replaced`.

## Review Focus

1. **Describe-queue cost and correctness.** All five backlogs must count a placement-backed image as described when its object holds a string description: the per-version records walk, the world library, the campaign library, the shell badge and the to-do counts.
   - They read an object only for a name with no legacy key, use the listing's own ids, and never resolve a blob.
   - Test: the counts are right with `image_refs.resolve_ref` patched to fail.
   - Test: a library image described through the route leaves the queue (B1).
2. **A legacy key and an object description disagree.** R1 shows the key. A write clears exactly the keys R2 names: world and campaign A cleared, campaign B kept. Tested in Tasks 1–2.
3. **The same bytes in two worlds.** The description is shared (R4); a subject tag stays in its own world. Tested in Task 3.
4. **Delete, re-create, fork.**
   - A deleted world or campaign whose slug is re-created, with the same picture placed again, starts untagged.
   - A fork carries its source's tags.
   - A failed fork leaves no scope behind.

   Tested in Task 4.
5. **An image used nowhere, or a malformed id.** 200 with every bucket empty, or 400. Never 404 or 500. Tested in Task 5.

---

### Task 1: Descriptions on the object (`image_descriptions`, library queues)

**Files:**
- Modify:
  - `backend/src/grimoire/store/image_store.py`: `update -> bool` (R8), `MAX_DESCRIPTION` (R9), `read_fresh`, and `read` memoized in its own statcache pool
  - `backend/src/grimoire/store/assets.py`: `names_in(..., with_refs=True)`; R12 in `put_in` / `link_in`
  - `backend/src/grimoire/store/image_descriptions.py`
  - `backend/src/grimoire/store/world_images.py`: `undescribed`, `has_undescribed`
  - `backend/src/grimoire/store/campaign_images.py`: `own_undescribed`
  - `backend/src/grimoire/store/world_bundle.py`: `MAX_IMPORTED_DESCRIPTION = image_store.MAX_DESCRIPTION`
  - the description PUT routes in `routes/characters.py`, `routes/worlds.py`, `routes/entities.py`, `routes/world_images.py` and `routes/campaigns.py`: 422 on "description too long"
  - `backend/tests/store_api_baseline.json`, only if a facade name changes
- Test:
  - `backend/tests/test_image_descriptions_store.py`
  - `backend/tests/test_world_images_store.py`
  - `backend/tests/test_campaign_images_store.py`
  - `backend/tests/test_image_store.py`
  - `backend/tests/test_image_description_routes.py`
  - `backend/tests/test_shell_route.py`

**Interfaces:**
- **Produces in `image_store`:**
  - `update(image_id, change) -> bool`. Every existing caller that ignored the return keeps working.
  - `MAX_DESCRIPTION = 4000`.
  - `read_fresh(image_id) -> ImageObject | None`, the same as `read` but bypassing statcache.
  - `read` memoizes in `statcache` pool `"image_objects"` (check `statcache.memo`'s pool parameter; if pools need registering, register one sized like the default).
- **Produces in `assets`:**
  - `names_in(d, also="", *, with_refs: bool = False)`. Without `with_refs` it returns `tuple[set[str], bool]` as today. With it, it returns `tuple[set[str], bool, dict[str, image_refs.Ref]]`, the refs from the one scan it already makes.
  - A backlog walk calls it once per folder and never scans placements a second time.
- **Produces in `image_descriptions`:**
  - `DescriptionTooLongError(ValueError)` (R9).
  - `text_in(d: Path, name: str, *, known_id: str | None | object = UNKNOWN) -> str | None` implements R1.
    - `UNKNOWN` (a module sentinel): read the placement.
    - `None`: the caller's listing row has no id (a legacy row), so read no object.
    - A string: use that id and read no placement.
    - The return value None means undescribed. A non-string legacy value counts as absent.
  - `legacy_text_in(d: Path, name: str) -> str | None` returns only the raw legacy key.
  - `read_in(d, names=None, ids: dict[str, str] | None = None) -> dict[str, str]` applies R1 per present name. It uses `ids` (name → image_id from the caller's listing) when given; otherwise it does one `image_refs.scan(d)`.
  - `set_in(d, name, text, names=None, *, also_clear: Path | None = None) -> None` implements R2, R8 and R9 for one directory.
  - `carry_legacy(d: Path, name: str, text: str) -> None` writes the legacy key only, under `sidecar_lock`.
  - `object_id_in(d: Path, name: str) -> str | None` returns the id of a **resolving** placement.
  - `described_names(d: Path, rows: list[dict]) -> set[str]`: given listing rows (`name`, optional `image_id`), the names that are R1-described. It reads an object only for a row with an `image_id` and no legacy key.
  - `catalog`, `undescribed`, `undescribed_count`, `undescribed_by_version` and `has_undescribed` keep their signatures, with R1-described semantics.
- **Consumers:** `world_images.undescribed`/`has_undescribed` and `campaign_images.own_undescribed` use `described_names` over their existing listing rows.

- [ ] **Step 1: Write the failing tests.** Give each image distinct bytes.
  - `test_update_reports_whether_it_wrote` (test_image_store): `True` on a write; `False` for an absent object; `False` when the callback returns None.
  - `test_a_placement_description_lives_on_the_object`: after `put_in(vdir, "avatar", b"png-1", "png")` and `set_in(vdir, "avatar", "A grey arch")`, the object holds the text and `descriptions.json` has no `avatar` key.
  - `test_a_shared_picture_shows_one_description_everywhere`: the same bytes are placed in two worlds' version dirs. A write to one reads in the other.
  - `test_a_local_legacy_key_wins_over_the_object`:
    - with the raw key `"Old"` and the object `"New"`, `text_in` is `"Old"`;
    - after `set_in(...,"Newer")`, `text_in` is `"Newer"` and the key is gone.
  - `test_reviewed_empty_on_the_object_is_described`: an object `""` is not in `undescribed`, and `catalog` gives `described=True, description=""`.
  - `test_an_unarrived_placement_with_a_legacy_file_writes_the_legacy_key`:
    - set up a legacy `avatar.png` beside a ref whose object is absent;
    - `set_in` writes `descriptions.json`;
    - `text_in` returns the text.
  - `test_an_unconfirmed_object_write_keeps_the_text_on_the_legacy_key` (B2): monkeypatch `image_store.update` to return `False` without writing. The text is in `descriptions.json`, and the old key's text is replaced, not lost.
  - `test_an_object_deleted_before_update_keeps_the_text`: delete the sidecar file from inside a wrapped `object_id_in` after it returns. The text lands in `descriptions.json`.
  - `test_description_too_long_is_its_own_error`: `DescriptionTooLongError` is a `ValueError` subclass, distinct from the unknown-name `ValueError`.
  - `test_set_in_clears_the_also_clear_directory`: cleared after a confirmed object write; kept after a legacy write.
  - `test_carry_legacy_writes_only_the_key`.
  - `test_set_in_refuses_an_overlong_description`: 4001 characters raise `ValueError`; exactly 4000 pass.
  - R12:
    - `test_put_in_drops_a_stale_caption_when_a_different_described_picture_replaces_it`;
    - converse: an undescribed new picture keeps the old key;
    - `test_a_first_placement_keeps_its_legacy_key`: a legacy `map.png` with key `"K"`, `put_in` of the same picture whose object has another world's text: `"K"` still reads;
    - `test_link_in_onto_an_empty_slot_keeps_a_carried_key`, the R5 order: carry the key, then `link_in` a described object, and the key still reads.
  - `test_backlogs_never_resolve_blobs`: patch `image_refs.resolve_ref` to raise. Then `undescribed_count`, `undescribed`, `has_undescribed`, `world_images.undescribed`, `world_images.has_undescribed` and `campaign_images.own_undescribed` are all correct, counting an object-described placement as described.
  - `test_a_world_library_image_described_through_set_in_leaves_the_queue` (B1, test_world_images_store), and the same for the campaign library (test_campaign_images_store).
  - `test_shell_undescribed_count_drops_after_describing_a_placement` (test_shell_route).
  - `test_description_put_over_the_cap_is_422` (test_image_description_routes): one record route and one library route.

- [ ] **Step 2:** run them; they fail.
- [ ] **Step 3:** implement. Inside `set_in`, under `sidecar_lock(d, DESCRIPTIONS_FILE)`:
  1. Check the name (today's rule).
  2. Check the cap.
  3. If `object_id_in(d, name)` returns an id, call `image_store.update(id, lambda raw: {**raw, "description": str(text)})`.
     - On `True`: `edit_sidecar(d, DESCRIPTIONS_FILE, {name: None})`, then the same on `also_clear` under its own `sidecar_lock`.
     - On `False`: today's raw read-modify-write in `d`.
  4. Otherwise: today's raw read-modify-write.
- [ ] **Step 4:** run them, plus `tests/test_image_descriptions_overlay.py tests/test_world_bundle.py tests/test_todo_route.py tests/test_world_gallery_route.py tests/test_campaign_gallery.py tests/test_assets_store.py`; they pass. A failure caused by a shared-bytes collision is fixed by making the bytes distinct. Any other failure is a bug.
- [ ] **Step 5:** commit: `feat(images): descriptions live on the image object; a local legacy key wins until migration`.

### Task 2: Overlay, libraries and D1

**Files:**
- Modify:
  - `backend/src/grimoire/store/overlay.py`: `read_description`, `read_descriptions`, `set_description`, the `promote_image` carry-up, `shadowed_images`
  - `backend/src/grimoire/store/campaign_images.py`: `read_descriptions`, `set_description`
  - `backend/src/grimoire/routes/campaigns.py`: `put_campaign_library_image_description` pre-check
- Test:
  - `backend/tests/test_image_descriptions_overlay.py`
  - `backend/tests/test_campaign_images_store.py`
  - `backend/tests/test_image_description_routes.py`

**Interfaces:**
- **Consumes:** Task 1's `text_in`, `legacy_text_in`, `read_in(ids=)`, `set_in(also_clear=)`, `carry_legacy`, `object_id_in`.
- **Produces:** unchanged public signatures. Behaviour:
  - **Visible text for a campaign name:**
    - when the campaign holds the image, or it is tombstoned, or the campaign is detached: `text_in(campaign dir, name, known_id=<row id or None>)`;
    - otherwise: the campaign's `legacy_text_in` if it is a string, else `text_in(world dir, name, known_id=<row id or None>)`.
    - The ids come from the `list_images` rows the overlay already builds. Pass them via `ids=`; never re-scan.
  - **`overlay.set_description` on an inherited name** (under `campaign_lock(cid)`, as today):
    - if `object_id_in(world dir, name)` resolves: `set_in(world_dir, name, text, names=union, also_clear=campaign_dir)`;
    - otherwise: the campaign legacy key, as today.
  - **`campaign_images.set_description`:** an inherited name whose world placement resolves writes the object, with `also_clear` set to the campaign library dir.
  - **The route pre-check:** answers 409 only when the name is inherited and `object_id_in(world library dir, name)` is None. Every other case calls the store.
  - **`overlay.promote_image`:** R5, with `legacy_text_in` deciding.

- [ ] **Step 1: Write the failing tests.**
  - `test_campaign_edit_clears_exactly_the_named_keys` (D1, Review Focus 2):
    - one inherited, placement-backed world avatar, with a world key `"W"`, a key `"C"` in campaign A and a key `"B"` in campaign B;
    - campaign A edits it to `"New"`;
    - the world and A read `"New"` with their keys gone, and B still reads `"B"`.
  - `test_campaign_legacy_override_masks_until_edited`.
  - `test_inherited_legacy_world_file_keeps_the_campaign_key`: the world holds a legacy file, and the campaign edit writes the campaign key, as today.
  - `test_campaign_library_route_describes_an_inherited_placement` (route): 200, and `world_images.read_descriptions(wid)` shows it. For a legacy world file the route answers 409.
  - `test_promote_carries_a_world_legacy_text_as_a_campaign_key`: covers both "world placement plus world key" and "world legacy file plus key". The object's `description` is unchanged.
  - `test_promote_does_not_carry_object_text`: no campaign key is written when the text came from the object.
  - `test_shadowed_rows_read_the_shared_description`.

- [ ] **Step 2:** run them; they fail.
- [ ] **Step 3:** implement. `read_descriptions` (the sweep) applies the same rule per name, using the listing ids.
- [ ] **Step 4:** run the three files, plus `tests/test_overlay.py tests/test_campaign_gallery.py tests/test_world_gallery_route.py tests/test_context_art.py`; they pass.
- [ ] **Step 5:** commit: `feat(images): a campaign description edit changes the shared image (D1)`.

### Task 3: Greeting subjects on the object (`image_subjects`)

**Files:**
- Modify:
  - `backend/src/grimoire/store/image_subjects.py`
  - `backend/src/grimoire/store/greeting_images.py`: `catalog` also returns each local key's slot
- Test:
  - `backend/tests/test_image_subjects_store.py`
  - the greeting subjects route tests (find the file that tests `put_world_greeting_subjects`)

**Interfaces:**
- **Consumes:** `greeting_images.catalog`, `image_refs.resolve`, `image_store.read`/`update` (`update -> bool`), and `worlds.paths.canonical_id`.
- **Produces:**
  - Same signatures: `read_subjects`, `set_image_subjects`, `reviewed_names`, `untagged`, `appearances`.
  - In `greeting_images`:
    - new internal `catalog_with_slots(root, gid) -> dict[str, tuple[dict, tuple[Path, str] | None]]`. It returns each entry plus its slot (own names, and local keys whose slot resolves), computed while it already resolves `local_path`, with no second record read.
    - `catalog(root, gid)` wraps it and returns only the entries, so its public shape is unchanged and no filesystem path reaches an API response.
    - `image_subjects` uses `catalog_with_slots`.
  - In `image_subjects`:
    - `_scope(root) -> str` returns `f"world:{canonical_id(root.name)}"` (R10).
    - `_placement_of(root, entry) -> tuple[Path, str] | None` returns the slot of the catalog entry when that slot lies **under `root`** and its placement resolves. A remote URL or a slot in another world's root gives None (R11).
- **Behaviour:**
  - Reads follow R1.
  - **Write to a key whose placement resolves:** `update` replaces this scope's character-subject associations with `cids` (deduplicated, sorted) and adds the scope to `reviews.subjects`. On `True` the `subjects.json` key is deleted; on `False` the sidecar is written, as today.
  - Remote URLs, cross-world keys and legacy names use `subjects.json`, as today.
  - `reviewed_names` = legacy keys ∪ keys whose object has the scope in `reviews.subjects`.
  - `appearances` visits **every** greeting directory, not only those that have a `subjects.json`.

- [ ] **Step 1: Write the failing tests.**
  - `test_tagging_a_placement_writes_scoped_associations`: the association `{"kind":"character","relation":"subject","scope":"world:realm","id":"seraphine"}` and `reviews.subjects == ["world:realm"]` are on the object, and there is no key in `subjects.json`.
  - `test_a_tag_shows_in_every_greeting_of_the_world_that_places_the_picture`.
  - `test_a_tag_is_absent_in_another_world` (Review Focus 3): the same bytes in `saltmarch` read as untagged.
  - `test_reviewed_empty_on_the_object_leaves_the_queue`.
  - `test_a_legacy_key_wins_until_retagged`.
  - `test_remote_and_cross_world_keys_stay_in_the_sidecar` (R11).
  - `test_scope_uses_the_canonical_world_id` (R10): `root.name` spelled `REALM` on a case-insensitive check. Simulate it by monkeypatching `canonical_id` if the test filesystem is case-sensitive.
  - `test_an_unconfirmed_object_write_keeps_the_sidecar_entry` (R8).
  - `test_appearances_finds_object_tags_without_a_sidecar`.
  - `test_untagged_and_appearances_routes_expose_no_slot` (route): no `slot` key in either response.

- [ ] **Step 2:** run them; they fail.
- [ ] **Step 3:** implement, with tolerant reads (a malformed association list or a non-string id is ignored).
- [ ] **Step 4:** run them, plus `tests/test_world_gallery_route.py tests/test_todo_route.py`; they pass.
- [ ] **Step 5:** commit: `feat(images): greeting subjects are scoped associations on the image object`.

### Task 4: Scope lifecycle (`image_scopes`), wired into delete and fork

**Files:**
- Create: `backend/src/grimoire/store/image_scopes.py`
- Modify:
  - `backend/src/grimoire/store/image_store.py`: `iter_ids`
  - `backend/src/grimoire/store/worlds/lifecycle.py`: `delete_world`, `fork_world`
  - `backend/src/grimoire/store/campaigns/lifecycle.py`: `delete_campaign`
  - `backend/src/grimoire/store/fork.py`: `fork_campaign`, `_discard`
  - `backend/src/grimoire/store/locks.py`: `"store.image_scopes"` in `DOMAIN_MODULES`
  - `backend/tests/test_lock_domain_guard.py` (R7)
  - `store/__init__.py`, `tests/store_api_baseline.json`
- Test: `backend/tests/test_image_scopes.py`, `backend/tests/test_world_fork.py`, `backend/tests/test_fork_store.py`

**Interfaces:**
- **Produces:**
  - `image_store.iter_ids() -> Iterator[str]`: every well-formed sidecar id under `objects/`, sorted. Malformed names are skipped.
  - `image_scopes.strip(scope: str) -> int`: removes that scope's associations and its `reviews.subjects` entry from every object. Objects are read with `read_fresh`, and `update` is called only where there is something to change. Returns the number changed. Idempotent.
  - `image_scopes.copy(src: str, dst: str) -> int`: adds `src`'s entries rewritten to `dst`, as a union.
  - `strip_world(wid)`, `copy_world(src_wid, dst_wid)`: no lock; scopes per R10.
  - `strip_campaign(cid)`: under `locks.campaign_lock(cid)`.
  - `copy_campaign(cid, new_cid)`: under `locks.hold_all([cid, new_cid])`, which is re-entrant inside `fork_campaign`'s hold.
- **Wiring:**
  - `delete_world`: `strip_world` after the `WorldInUse` check, before `rmtree`.
  - `delete_campaign`: `strip_campaign` before `rmtree` (R13 documents the new lock).
  - `fork_world`:
    - restructure it so `publish(...)`'s result is captured, then call `copy_world(wid, new_wid)` after the `staging_tree` block;
    - a failure in `copy_world` is logged through `logs.record` and does not fail the fork. The fork is published and usable, it is only missing its tags (documented).
  - `fork_campaign`:
    - call `copy_campaign` **inside** the try that `_discard`s on failure;
    - `_discard(new_cid)` also calls `image_scopes.strip(f"campaign:{new_cid}")`, best effort, before removing the tree.
- **R7, in `test_lock_domain_guard.py`:**
  - add `image_store` to the receiver-namespace map (`_fs_namespaces`), with `update` and `merge_projection` as writes;
  - add the `from .image_store import update` form to `_imported_writers`;
  - add self-tests through the guard's `_probe(src)` helper:
    - `image_store.update(...)` and `merge_projection(...)` in a `cid` function count as writes;
    - `image_store.read(...)` does not;
    - an unlocked `def strip_campaign(cid): image_store.update(...)` probe is reported.

- [ ] **Step 1: Write the failing tests.**
  - `test_strip_removes_only_that_scope`.
  - `test_copy_unions_into_the_new_scope`.
  - `test_a_recreated_world_slug_starts_untagged` (Review Focus 4):
    1. tag a greeting's placement in `realm`;
    2. `delete_world("realm")`;
    3. `create_world("Realm")`;
    4. create a greeting and place the same bytes.

    Then `read_subjects` is untagged, and `image_store.read(id).raw` has no `world:realm` in `associations` or `reviews.subjects`.
  - `test_forking_a_world_carries_its_tags`.
  - `test_forking_a_campaign_copies_campaign_scope`: seed a `campaign:<cid>` association with `update`.
  - `test_deleting_a_campaign_strips_campaign_scope`.
  - `test_a_failed_campaign_fork_leaves_no_scope`: inject a failure after `copy_campaign` has written. The new scope is absent and the fork is discarded.
  - The R7 guard self-tests above, plus the existing guard suite passing with `image_scopes` classified.

- [ ] **Step 2:** run them; they fail.
- [ ] **Step 3:** implement.
- [ ] **Step 4:** run `tests/test_image_scopes.py tests/test_world_fork.py tests/test_fork_store.py tests/test_lock_domain_guard.py tests/test_lock_order_guard.py tests/test_import_guard.py tests/test_store_api_baseline.py tests/test_campaigns_store.py`; they pass.
- [ ] **Step 5:** commit: `feat(images): deleting a world or campaign strips its scope; forking copies it`.

### Task 5: Derived usage (`image_usage`) and `GET /api/images/{image_id}/usage`

**Files:**
- Create:
  - `backend/src/grimoire/store/image_usage.py`
  - `backend/src/grimoire/routes/images.py` (`router`)
- Modify:
  - `backend/src/grimoire/routes/__init__.py`: import it, and add `images` to the compose tuple after `world_images`
  - `store/__init__.py`, `tests/store_api_baseline.json`
- Test: `backend/tests/test_image_usage.py`, `backend/tests/test_route_order.py` (stays green)

**Interfaces:**
- **Produces `image_usage.find(image_id: str) -> dict[str, list[dict]]`:**
  - exactly the keys `characters, pcs, entities, greetings, world_images, campaign_images, covers, collections`;
  - each list sorted by `json.dumps(entry, sort_keys=True)`;
  - entry shapes:
    - `characters` / `pcs`: `{"scope", "id", "vid", "name"}`;
    - `entities`: `{"scope", "kind", "id", "vid", "name"}`;
    - `greetings`: `{"scope", "id", "name"}`;
    - `world_images`: `{"wid", "name"}`;
    - `campaign_images`: `{"cid", "name"}`;
    - `covers`: `{"scope"}`;
    - `collections`: `{"wid", "collection"}` (a format-1 manifest member whose world-library placement has this id).
- **Classification:**
  - Roots are every directory directly under `paths.home()/"worlds"` and `paths.home()/"campaigns"`, walked directly (as `world_refs` does), not through `list_worlds`/`list_campaigns`.
  - `image_refs.walk(root)` yields owning dirs; each is matched against the path builders (`assets.version_dir` shapes per base, including every `entities.ENTITY_KINDS` kind and greetings; the world and campaign library dirs; the cover dir).
  - An owning dir matching no builder is skipped.
  - Scope strings follow R10.
- **Route:** `GET /api/images/{image_id}/usage`:
  - 400 when `not image_hash.is_image_id(image_id)`;
  - otherwise 200 with `find(image_id)`;
  - it stamps no campaign revision.
- **Note** in the module docstring: this is an on-demand full walk. §9's rebuildable cache is the escape hatch if profiling ever demands one.

- [ ] **Step 1: Write the failing tests.**
  - `test_usage_finds_every_surface`: parameterized over `tests/test_image_surfaces.py`'s `SURFACES`; each surface's placement appears in the right bucket with the right ids.
  - `test_usage_is_empty_for_an_unknown_id` (Review Focus 5): 200, all eight buckets `[]`.
  - `test_usage_refuses_a_malformed_id`: 400.
  - `test_usage_ignores_image_less_placements`.
  - `test_usage_lists_a_format1_collection_member`.
- [ ] **Step 2:** run them; they fail.
- [ ] **Step 3:** implement.
- [ ] **Step 4:** run `tests/test_image_usage.py tests/test_route_order.py tests/test_import_guard.py tests/test_store_api_baseline.py`; they pass.
- [ ] **Step 5:** commit: `feat(images): derived image usage and its route`.

### Task 6: Art recall dedupes by image

**Files:**
- Modify: `backend/src/grimoire/store/context/art.py` (`_record_candidates`, `_base_pass_through`, `_library_candidates`, `candidates`)
- Test: `backend/tests/test_context_art.py`

**Interfaces:**
- **Consumes** the `image_id` already on `overlay.list_images` / `overlay.base_versions` rows and `campaign_images.list_images` rows. No per-candidate placement or object read is added.
- **Produces:**
  - a candidate row gains `"image_id"` when its listing row had one;
  - `candidates` keeps the **first** row per `image_id`, in its existing tier order (cast, location, entities, library), after its existing `(kind, id, name)` dedupe;
  - rows without an id are never deduped;
  - handles and URLs are unchanged.

- [ ] **Step 1: Write the failing tests.**
  - `test_one_picture_in_cast_and_library_is_offered_once_as_the_cast_handle`.
  - `test_legacy_rows_are_not_deduped`.
  - `test_dedupe_is_deterministic_across_calls`.
  - `test_candidates_never_resolve_a_blob`: patch `image_refs.resolve_ref` to count calls; the count does not grow beyond what the listings already make.
- [ ] **Step 2:** run them; they fail.
- [ ] **Step 3:** implement.
- [ ] **Step 4:** run `tests/test_context_art.py tests/test_art_handles_persist.py` and the evals in `pytest backend -k evals`; they pass.
- [ ] **Step 5:** commit: `feat(images): art recall offers one handle per picture`.

### Task 7: "Used in" panel (frontend)

**Files:**
- Modify:
  - `frontend/src/api/types.ts`: `ImageUsageReport` and `ImageUsageEntry`, matching Task 5's shapes exactly
  - `frontend/src/api/client.ts`: `getImageUsage(imageId: string): Promise<ImageUsageReport>` → `GET /api/images/${encodeURIComponent(imageId)}/usage`
- Create:
  - `frontend/src/components/ImageUsage.tsx`
  - `frontend/src/components/ImageUsage.test.tsx`
  - `frontend/src/components/character/ArtTab.test.tsx` (no ArtTab test exists today)
- Modify:
  - `components/character/ArtTab.tsx`, `components/pc/PCArtTab.tsx`, `components/EntityEditor.tsx`, `components/WorldArtPanel.tsx`, `components/PostImagePicker.tsx`: render `<ImageUsage imageId={…} />` beside each `ImageDescriptionField` whose row has an `image_id`.
  - `PostImagePicker.tsx` (R3): show the description field for an inherited library image that has an `image_id`, and fix the comment that says the route refuses it.

**Interfaces:**
- **`ImageUsage({ imageId, navigable = true })`:**
  - **Collapsed:** a button "Used in…". Nothing is fetched.
  - **Opened:**
    - it calls `api.getImageUsage` once;
    - it renders a `.side-section` with `<h4>Used in</h4>` and one chip per entry, grouped by bucket;
    - each label is `<record id> · <name>`, or the collection or library name;
    - errors use `.field-hint`.
  - **Navigation:**
    - a chip is a `<button className="chip">` that `useNavigate`s when the bucket has a page and `navigable` is true; otherwise it is a `<span className="chip on">`;
    - `PostImagePicker` passes `navigable={false}`, because it is a modal over a composer.
  - **Bucket → route** (check each one against `src/App.tsx` and correct any path that differs, keeping one test per bucket):
    - characters: `world:<wid>` → `/worlds/<wid>/characters/<id>`; `campaign:<cid>` → `/campaigns/<cid>/world/characters/<id>`;
    - pcs: `world:` → `/worlds/<wid>/pcs/<id>`; `campaign:` → `/campaigns/<cid>/world/pcs/<id>`;
    - entities: `world:` → `/worlds/<wid>/<kind>/<id>`; `campaign:` → the matching `/campaigns/<cid>/world/<kind>/<id>` route if App.tsx has one, else non-clickable;
    - greetings → `/worlds/<wid>/greetings/<id>`;
    - world_images → the world art page;
    - campaign_images → the campaign images page;
    - covers and collections → non-clickable.
  - No keyboard binding.
  - The read-only "World images" tiles in ArtTab render no `ImageDescriptionField`, so they get no "Used in" control. This is intended: the panel sits beside the editable description.

- [ ] **Step 1: Write the failing tests.**
  - `ImageUsage.test.tsx`:
    - `stays collapsed and fetches nothing until opened`;
    - `lists every bucket's entries as chips after opening`;
    - `each bucket with a page navigates to it` (table-driven);
    - `non-navigable chips render as spans`;
    - `shows an error hint when the request fails`.
  - `ArtTab.test.tsx`: `an image with an image_id shows the Used in control; a legacy image does not`.
  - The PostImagePicker test: `an inherited placement-backed library image is describable`.
- [ ] **Step 2:** run them (`npx vitest run src/components/ImageUsage.test.tsx …`); they fail.
- [ ] **Step 3:** implement.
- [ ] **Step 4:** run them, plus `npx tsc --noEmit -p .`, the touched components' tests and `make check-eslint` (at baseline); they pass.
- [ ] **Step 5:** commit: `feat(images): a "Used in" panel beside every image description`.

### Task 8: Docs, spec amendments, gate

**Files:**
- Modify:
  - `docs/superpowers/specs/2026-10-05-content-addressed-image-store-design.md`: add "### Stage-2 amendments" after the follow-up amendments, with R1–R13 as one bullet each.
  - `docs/store-guarantees.md` (image section):
    - descriptions are global, on the object;
    - subjects are scoped;
    - R1/R2/R8;
    - delete strips a scope and fork copies it;
    - usage is derived;
    - the R13 notes, including that a global description travels in other worlds' bundles.
  - `CLAUDE.md`: one sentence in the "Images live in…" note: a picture's description and subject tags live on its object, legacy sidecars are read first until migration, and descriptions are global.
  - Module docstrings that now contradict the code:
    - the top of `image_descriptions` (including the "replaced image keeps old text" paragraph, now R12);
    - the top of `image_subjects`;
    - `overlay.read_description`;
    - `campaign_images.read_descriptions`;
    - `world_images` (the unlocked-write note);
    - `staging.repoint_urls` (both the subjects note and R13's description note).
- Test: `backend/tests/test_docs_guard.py`. In its existing style, require that the image section names `image_scopes` and `image_usage` and that both modules exist.

- [ ] **Step 1:** write the docs-guard assertion; it fails.
- [ ] **Step 2:** write the docs and the amendments; the guard passes.
- [ ] **Step 3:** run the full gate with `make check PY=backend/.venv/bin/python`. Only the allowed root-only failure is acceptable, the ratchets stay at baseline, and the frozen snapshot is unchanged.
- [ ] **Step 4:** commit: `docs(images): stage-2 amendments and guarantees`.
