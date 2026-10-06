# Content-Addressed Image Store — Stage 2 (Shared Metadata) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Facts about a picture live once, on its image object.
- The description is global, shared by every placement in every world.
- Subject tags are scoped per world or campaign.
- Where a picture is used is derived on demand, and shown in a "Used in" panel.

Legacy sidecar entries keep showing exactly what they show today until stage 4 migrates them.

**Architecture:**
- **No new store of truth.** Descriptions and greeting subjects are read and written through the existing façades (`image_descriptions`, `image_subjects`, `overlay`, `campaign_images`, `world_images`). Each façade routes a placement-backed name to the object through `image_store.update`, and keeps legacy names on their sidecars.
- **Scopes:** a new `image_scopes` module strips a world's or campaign's scope from every object when it is deleted, and copies the scope when it is forked.
- **Usage:** a new `image_usage` module derives where an image is used by walking placements. A new `/api/images` router serves it, and a small frontend panel shows it.
- **Art recall:** `context.art` dedupes its candidates by `image_id`.

**Tech Stack:** Python 3.11+, FastAPI, pytest; React + TypeScript, vitest.

**Spec:** `docs/superpowers/specs/2026-10-05-content-addressed-image-store-design.md`. This is stage 2 of §14; read §3, §4, §6, §9 and §13 ("Metadata"). The stage-2 rulings below become a "Stage-2 amendments" section of the spec in Task 8.

## Stage-2 rulings (decided with the user; the spec gains them in Task 8)

**R1. Precedence: the local legacy entry wins until migration.**
- **One directory `d`, image-bearing placement `name`:** the text shown is
  1. `descriptions.json[name]` in `d`, if that key is present;
  2. otherwise the object's `description`, if it is a string;
  3. otherwise the image is undescribed.

  A legacy name with no placement reads only its key.
- **Subjects** follow the same rule. For a greeting image, the `subjects.json[key]` list wins when present. Otherwise the object is used: when `world:<wid>` is in `reviews.subjects`, the subjects are the `id`s of its associations with `kind == "character"`, `relation == "subject"` and `scope == world:<wid>`. Otherwise the image is unreviewed.

**R2. A write goes to the object behind the visible placement.**
- **When that object resolves:** `image_store.update` sets the field, and then the name's legacy key is deleted in (a) the directory being edited and (b) the directory of the visible placement, if different. The edit then shows in both places.
- **When the placement has not arrived** (an object or blob not synced), the write goes to the legacy key of the edited directory, exactly as today. R1 shows it, and no text is ever lost.
- **When the name has no placement,** legacy is used, as today.
- **Order:** the object first, then the key deletions. A crash in between shows the previous text (R1) and never loses the new one.

**R3. D1, inherited campaign edits.**
- A campaign edit of an image it inherits, whether on a record or in the library, writes the shared object, per R2.
- The campaign library route's 409 ("describe this image in its world") stays only for an inherited library image the world holds as a **legacy file**.

**R4. Descriptions are global.** One picture has one description, in every world. Subject associations stay scoped.

**R5. Promotion carry-up.** `overlay.promote_image` keeps carrying the visible text into the campaign, but as a **legacy key**, through `image_descriptions.carry_legacy`, and only when the text did not come from the object. The campaign's view is unchanged, and the shared object is not touched by a promote.

**R6. "Used in" panel.** `GET /api/images/{image_id}/usage` plus a collapsed "Used in…" control beside every `ImageDescriptionField`. It fetches only when opened.

**R7. Lock-domain guard.** `test_lock_domain_guard.py` learns that `image_store.update` and `image_store.merge_projection` are writes, so `image_scopes` is surveyed and can sit in `DOMAIN_MODULES`.

## Global Constraints

- **Privacy:** invented names only (Seraphine, Mara, Winifred, Realm, Saltmarch). Never describe a real store's contents in a commit, comment, fixture or doc.
- **Writes and paths:** every file write goes through `store.atomic`, and every object write through `image_store.update`, whose callback is a pure dict edit (no store calls, no campaign or ingest/GC lock; the latch enforces it). No new `Path.home()`.
- **Imports:**
  - At module scope, and the graph stays acyclic.
  - `image_store` and `image_refs` import nothing above them.
  - `image_descriptions` may not import `characters`, `overlay` or `campaign_images` (the reverse imports exist).
  - `image_scopes` and `image_usage` import only `image_store`, `image_refs`, `locks`, `paths` and the path builders they need. Each binds submodules.
- **Scopes:** strings `world:<wid>` or `campaign:<cid>`. A bare id is never written.
- **Associations:** `{"kind": "character", "relation": "subject", "scope": <scope>, "id": <cid>}` (all values strings). `reviews` is `{"subjects": [<scope>, ...]}`, sorted and deduplicated.
- **Every new store module** goes into `store/__init__.py`'s import list and the `"dir"` list of `backend/tests/store_api_baseline.json` (not `"all"`).
- **The frozen campaign `snapshot.json` must not change.** Its store holds legacy files only, and R1 reads them as today.
- **Test fixtures that share bytes now share an object.** Many store tests upload the same opaque `b"png"` bytes for different images, and since R4 those images share one description. Where a test needs distinct texts, give each image distinct bytes (`b"png-1"`, `b"png-2"`, …). Never weaken an assertion to make a shared-object collision pass.
- **Ratchets** (ruff, mypy, eslint) stay at baseline. If one improves, run `make baseline` and commit the smaller file.
- **The docs guard** stays green. Anything a doc claims must hold.
- **How to run things:**
  - Backend tests from `backend/`: `PYTHONPATH=src .venv/bin/python -m pytest <path> -q`.
  - Frontend from `frontend/`: `npx vitest run <path>` and `npx tsc --noEmit -p .`.
  - Full gate: `make check`. The only allowed failure is the root-only `test_atomic.py::test_a_read_only_record_is_not_silently_replaced`.

## Review Focus

1. **Undescribed backlog and shell badge cost.** `_undescribed_names` and `has_undescribed` now need each placement's id and the object's `description` for every version folder that holds placements. They must use one `image_refs.scan(vdir)` per folder that has placements, plus memoized `image_store.read`. Never resolve a blob, and never `list_in`. Test: a folder whose placements' blobs are missing still counts correctly, and no blob path is stat'ed (patch `image_refs.resolve_ref` to fail).
2. **A legacy key and an object description disagree** (stage-1 data). R1 must show the key, and a write must clear exactly the keys R2 names and no other directory's. Test this in Task 1 and in Task 2 (campaign inherited).
3. **Shared opaque bytes.** Two placements of identical bytes in different worlds: describing one changes the other (R4), but a subject tag in world A never shows in world B. Test in Task 3.
4. **Deleting a world or campaign whose slug is then re-created** must not inherit the old tags; a fork must carry them. Test the re-create case in Task 4.
5. **An image used nowhere, or with a malformed id.** The usage route answers 400 for a malformed id. An unknown well-formed id gets 200 with every bucket empty, never 404 or 500. Test in Task 5.

---

### Task 1: Descriptions on the object (`image_descriptions`)

**Files:**
- Modify: `backend/src/grimoire/store/image_descriptions.py`
- Test: `backend/tests/test_image_descriptions_store.py`

**Interfaces:**
- Consumes:
  - `image_refs.read(d, name) -> Ref | None`, `image_refs.scan(d) -> dict[str, Ref]`, `image_refs.resolve(d, name)`;
  - `image_store.read(id) -> ImageObject | None` (`.raw`);
  - `image_store.update(id, change)`;
  - `assets.sidecar_lock`, `assets.edit_sidecar`, `DESCRIPTIONS_FILE`.
- Produces:
  - `text_in(d: Path, name: str) -> str | None`: R1 for one directory. None means undescribed. A non-string legacy value counts as absent, as `read_in` does today.
  - `read_in(d, names=None) -> dict[str, str]`: same signature, now R1 per present name.
  - `set_in(d, name, text, names=None) -> None`: R2 for one directory (the edited and visible directory are the same). It also gains `also_clear: Path | None = None`, a second directory whose legacy key for `name` is deleted after an object write.
  - `carry_legacy(d: Path, name: str, text: str) -> None`: writes the legacy key only, under `sidecar_lock`. Used by R5.
  - `object_id_in(d: Path, name: str) -> str | None`: the id of a **resolving** placement in `d`, else None.
  - `catalog`, `undescribed`, `undescribed_count`, `undescribed_by_version` and `has_undescribed` keep their signatures. "Described" is now R1-present.

- [ ] **Step 1: Write the failing tests** in `test_image_descriptions_store.py`. Give each image distinct bytes.
  - `test_a_placement_description_lives_on_the_object`: after `put_in(vdir, "avatar", b"png-1", "png")` and `set_in(vdir, "avatar", "A grey arch")`, `image_store.read(id).raw["description"] == "A grey arch"` and `descriptions.json` has no `avatar` key.
  - `test_a_shared_picture_shows_one_description_everywhere`: the same bytes are placed in two worlds' version dirs. Setting it in one makes `read_in` return it in the other.
  - `test_a_local_legacy_key_wins_over_the_object`: with the key written raw as `"Old"` and the object set to `"New"` via `update`, `text_in` is `"Old"`. After `set_in(...,"Newer")`, `text_in` is `"Newer"` and the key is gone.
  - `test_reviewed_empty_on_the_object_is_described`: an object description of `""` is excluded from `undescribed`, and `catalog` gives `described=True, description=""`.
  - `test_an_unarrived_placement_writes_the_legacy_key`: a ref is written whose object is absent. `set_in` writes `descriptions.json` and `text_in` returns the text.
  - `test_set_in_clears_the_also_clear_directory`: the key in the second directory is deleted after an object write and kept after a legacy write.
  - `test_carry_legacy_writes_only_the_key`: the object is unchanged.
  - `test_undescribed_never_resolves_blobs`: `monkeypatch.setattr(image_refs, "resolve_ref", boom)`. `undescribed_count`, `undescribed` and `has_undescribed` still answer, counting a placement with an object description as described.

- [ ] **Step 2:** run them and see them fail (`AttributeError` / assertion).

- [ ] **Step 3:** implement.
  - `object_id_in` uses `image_refs.resolve(d, name)`.
  - `text_in` and `read_in` use the legacy raw dict first, then `image_store.read(ref.image)` for names whose placement **has an `image`, resolving or not**. The object is read even when the blob is missing, since a description needs no blob.
  - `set_in` keeps its existing name check, under `sidecar_lock(d, DESCRIPTIONS_FILE)`.
    - If `object_id_in(d, name)` returns an id: call `image_store.update(id, lambda raw: {**raw, "description": str(text)})`. Then drop `name` from `d`'s sidecar with `edit_sidecar(d, DESCRIPTIONS_FILE, {name: None})`, and from `also_clear`'s too (taking that directory's `sidecar_lock`).
    - Otherwise: today's raw read-modify-write.
  - The backlog walks get ids from one `image_refs.scan(vdir)` per folder; only when `names_in` reported placements.

- [ ] **Step 4:** run the file and see it pass. Also run `tests/test_image_descriptions_overlay.py tests/test_image_description_routes.py tests/test_world_images_store.py tests/test_campaign_images_store.py`. Where a failure is a shared-bytes collision, make the bytes distinct; any other failure is a bug.

- [ ] **Step 5:** commit: `feat(images): descriptions live on the image object; a local legacy key wins until migration`.

### Task 2: Overlay, libraries and D1 (campaign edits change the shared object)

**Files:**
- Modify: `backend/src/grimoire/store/overlay.py` (`read_description`, `read_descriptions`, `set_description`, `promote_image` carry-up, `shadowed_images`)
- Modify: `backend/src/grimoire/store/campaign_images.py` (`read_descriptions`, `set_description`)
- Modify: `backend/src/grimoire/routes/campaigns.py` (`put_campaign_library_image_description`, the 409 condition)
- Test: `backend/tests/test_image_descriptions_overlay.py`, `backend/tests/test_campaign_images_store.py`, `backend/tests/test_image_description_routes.py`

**Interfaces:**
- Consumes: Task 1's `text_in`, `set_in(..., also_clear=)`, `carry_legacy`, `object_id_in`.
- Produces: unchanged public signatures. Behaviour:
  - **Visible text for a campaign name:**
    - if the campaign holds the image, or it is tombstoned, or the campaign is detached: `text_in(campaign dir, name)`;
    - otherwise: the campaign's legacy key if present, else `text_in(world dir, name)`.
  - **`overlay.set_description` on an inherited name:**
    - if the world's placement resolves: `set_in(world_dir, name, text, names=union, also_clear=campaign_dir)`, under `campaign_lock(cid)` as today;
    - otherwise: the campaign legacy key, as today.
  - **`campaign_images.set_description`:** an inherited name whose world placement resolves writes the object (R3). An inherited legacy world file raises the existing error, which the route maps to 409.
  - **`overlay.promote_image`:** carries the visible text with `carry_legacy` only when it came from a legacy key (R5).

- [ ] **Step 1: Write the failing tests.**
  - `test_campaign_edit_of_inherited_art_changes_the_world` (D1): `overlay.set_description` on an inherited, placement-backed avatar. `image_descriptions.text_in(world_dir, "avatar")` then equals the new text, and neither directory keeps a legacy key.
  - `test_campaign_legacy_override_still_masks_until_edited`: the campaign key `"Override"` and an object `"Shared"` read as `"Override"` through `read_description`. After an edit, both read as the new text.
  - `test_inherited_legacy_world_file_keeps_the_campaign_key`: the world holds a legacy file, and the campaign edit writes the campaign key, as today.
  - `test_campaign_library_inherited_placement_is_describable` (route): `PUT /campaigns/{cid}/images/{name}/description` on an inherited, placement-backed world library image returns 200, and `world_images.read_descriptions(wid)` shows it. A legacy world file still returns 409.
  - `test_promote_carries_a_world_legacy_text_as_a_campaign_key`: the visible text from a world legacy key survives `overlay.promote_image`, and the object's `description` is unchanged.
  - `test_shadowed_rows_read_the_shared_description`.

- [ ] **Step 2:** run them and see them fail.
- [ ] **Step 3:** implement as in Interfaces. `read_descriptions` (the sweep) applies the same rule per name.
- [ ] **Step 4:** run the three files plus `tests/test_overlay.py tests/test_campaign_gallery.py tests/test_world_gallery_route.py`, and see them pass.
- [ ] **Step 5:** commit: `feat(images): a campaign description edit changes the shared image (D1)`.

### Task 3: Greeting subjects on the object (`image_subjects`)

**Files:**
- Modify: `backend/src/grimoire/store/image_subjects.py`
- Test: `backend/tests/test_image_subjects_store.py`, `backend/tests/test_greeting_routes.py` (or wherever `put_world_greeting_subjects` is tested)

**Interfaces:**
- Consumes:
  - `greeting_images.catalog(root, gid)` (keys: own names, local `/api/worlds/...` paths, remote URLs);
  - `greeting_images.local_slot(root, url) -> (dir, name) | None`;
  - `image_refs.resolve`, `image_store.read`/`update`.
- Produces (same signatures): `read_subjects`, `set_image_subjects`, `reviewed_names`, `untagged`, `appearances`. Plus:
  - `_placement_of(root, gid, key) -> tuple[Path, str] | None`: the placement directory and name behind a catalog key. For an own name it is the greeting's own version dir; for a local key it is `local_slot`; for a remote URL it is None.
  - The scope is `f"world:{root.name}"`.
- Behaviour:
  - Reads follow R1 for subjects.
  - **A write to a key whose placement resolves:** `update` replaces this scope's character-subject associations with `cids` (deduplicated, sorted) and adds the scope to `reviews.subjects`; then the `subjects.json` key is deleted.
  - **Remote URLs and legacy names** use `subjects.json`, as today.
  - `reviewed_names` = legacy keys ∪ keys whose object has the scope in `reviews.subjects`.
  - `appearances` must also visit greetings **without** a `subjects.json` whose images carry object associations. Iterate every greeting directory, not the `glob` of sidecars.

- [ ] **Step 1: Write the failing tests.**
  - `test_tagging_a_placement_writes_scoped_associations`: the object gets an association `{"kind":"character","relation":"subject","scope":"world:realm","id":"seraphine"}` and `reviews.subjects == ["world:realm"]`, and `subjects.json` has no key.
  - `test_a_tag_in_one_greeting_shows_in_another_greeting_of_the_same_world`: the same image is placed in two greetings, both in `read_subjects`.
  - `test_a_tag_is_absent_in_another_world` (Review Focus 3): the same bytes in a world `saltmarch` greeting read as untagged there.
  - `test_reviewed_empty_on_the_object_leaves_the_queue`: `set_image_subjects(..., [])` removes it from `untagged`.
  - `test_a_legacy_key_wins_until_retagged`.
  - `test_remote_url_subjects_stay_in_the_sidecar`.
  - `test_appearances_finds_object_tags_without_a_sidecar`.

- [ ] **Step 2:** run them and see them fail.
- [ ] **Step 3:** implement. Tolerant reads: a non-list association set or a non-string id is ignored, as `read_subjects` ignores malformed sidecar values today.
- [ ] **Step 4:** run them plus `tests/test_world_gallery_route.py tests/test_todo_route.py`, and see them pass.
- [ ] **Step 5:** commit: `feat(images): greeting subjects are scoped associations on the image object`.

### Task 4: Scope lifecycle (`image_scopes`), wired into delete and fork

**Files:**
- Create: `backend/src/grimoire/store/image_scopes.py`
- Modify: `backend/src/grimoire/store/image_store.py` (add `iter_ids`)
- Modify:
  - `backend/src/grimoire/store/worlds/lifecycle.py` (`delete_world`, `fork_world`)
  - `backend/src/grimoire/store/campaigns/lifecycle.py` (`delete_campaign`)
  - `backend/src/grimoire/store/fork.py` (`fork_campaign`, inside its `hold_all`)
- Modify:
  - `backend/src/grimoire/store/locks.py` (add `"store.image_scopes"` to `DOMAIN_MODULES`)
  - `backend/tests/test_lock_domain_guard.py` (R7)
- Modify: `store/__init__.py`, `tests/store_api_baseline.json`
- Test: `backend/tests/test_image_scopes.py`, `backend/tests/test_world_fork.py`, `backend/tests/test_fork_store.py`

**Interfaces:**
- Produces:
  - `image_store.iter_ids() -> Iterator[str]`: every well-formed sidecar id under `objects/`, in sorted order. Malformed names are skipped.
  - `image_scopes.strip(scope: str) -> int`: removes the scope's associations and its `reviews.subjects` entry from every object that has either. Returns the number of objects changed; idempotent.
  - `image_scopes.copy(src: str, dst: str) -> int`: for every object with `src` associations or reviews, adds the same entries rewritten to `dst` (a union, never removing).
  - `image_scopes.strip_world(wid: str) -> int` and `copy_world(src_wid: str, dst_wid: str) -> int`: no lock (worlds have none).
  - `image_scopes.strip_campaign(cid: str) -> int`: under `locks.campaign_lock(cid)`.
  - `image_scopes.copy_campaign(cid: str, new_cid: str) -> int`: under `locks.hold_all([cid, new_cid])`. It is re-entrant, so `fork_campaign` calls it inside its own hold.
- Wiring:
  - `delete_world` calls `strip_world` after the `WorldInUse` check and before the `rmtree`;
  - `delete_campaign` calls `strip_campaign` before the `rmtree`;
  - `fork_world` calls `copy_world(wid, new_wid)` after `publish` returns the final id;
  - `fork_campaign` calls `copy_campaign` after `_copy`, inside the hold.
- R7: in `test_lock_domain_guard.py`, add `image_store.update` and `image_store.merge_projection` to the recognized writer calls, next to `_ASSETS_WRITERS`.

- [ ] **Step 1: Write the failing tests.**
  - `test_strip_removes_only_that_scope`;
  - `test_copy_unions_into_the_new_scope`;
  - `test_deleting_a_world_strips_its_tags_and_a_recreated_slug_starts_clean` (Review Focus 4): tag, `delete_world`, `create_world` with the same name, `read_subjects` is untagged;
  - `test_forking_a_world_carries_its_tags`;
  - `test_forking_a_campaign_copies_campaign_scope` (seed a `campaign:<cid>` association with `update`);
  - `test_deleting_a_campaign_strips_campaign_scope`;
  - in `test_lock_domain_guard`, the existing suite must pass with `image_scopes` classified. Prove the guard really sees it: a temporary copy with the lock removed must fail, demonstrated once in the test via the guard's own survey helper on a source string, if the guard exposes one. Otherwise note it in the report.

- [ ] **Step 2:** run them and see them fail.
- [ ] **Step 3:** implement. Each object edit is one `update` whose callback filters lists. Read with `image_store.read` first and call `update` only when there is something to change.
- [ ] **Step 4:** run `tests/test_image_scopes.py tests/test_world_fork.py tests/test_fork_store.py tests/test_lock_domain_guard.py tests/test_lock_order_guard.py tests/test_import_guard.py tests/test_store_api_baseline.py`, and see them pass.
- [ ] **Step 5:** commit: `feat(images): deleting a world or campaign strips its scope; forking copies it`.

### Task 5: Derived usage (`image_usage`) and `GET /api/images/{image_id}/usage`

**Files:**
- Create: `backend/src/grimoire/store/image_usage.py`
- Create: `backend/src/grimoire/routes/images.py` (`router`)
- Modify: `backend/src/grimoire/routes/__init__.py` (import it, and add `images` to the compose tuple after `world_images`)
- Modify: `store/__init__.py`, `tests/store_api_baseline.json`
- Test: `backend/tests/test_image_usage.py` (store, and the route via the `client` fixture), `backend/tests/test_route_order.py` (must stay green)

**Interfaces:**
- Produces:
  - `image_usage.find(image_id: str) -> dict[str, list[dict]]`, with exactly the keys `characters, pcs, entities, greetings, world_images, campaign_images, covers, collections`, each sorted. Entry shapes:
    - `characters` / `pcs`: `{"scope", "id", "vid", "name"}`;
    - `entities`: `{"scope", "kind", "id", "vid", "name"}`;
    - `greetings`: `{"scope", "id", "name"}`;
    - `world_images`: `{"wid", "name"}`;
    - `campaign_images`: `{"cid", "name"}`;
    - `covers`: `{"scope"}`;
    - `collections`: `{"wid", "collection"}` (a format-1 manifest whose member name's world-library placement has this id).
  - **Classification:** use `image_refs.walk(root)` over every world root and campaign root (the existing listing helpers) and match each owning directory against the path builders (`assets.version_dir` shapes per base, the world and campaign library dirs, the cover dir). An owning directory that matches no builder is skipped, never misfiled. Scope strings follow the Global Constraints.
  - **Route** `GET /api/images/{image_id}/usage`: 400 when `not image_hash.is_image_id(image_id)`; otherwise 200 with `find(image_id)`. It is a GET, so it stamps no campaign revision.

- [ ] **Step 1: Write the failing tests.**
  - `test_usage_finds_every_surface`: parameterized over the stage-1 roster in `tests/test_image_surfaces.py` (import its `SURFACES`). Place one image per surface and assert it shows up in the matching bucket with the right ids.
  - `test_usage_is_empty_for_an_unknown_id` (Review Focus 5): 200, all eight buckets `[]`.
  - `test_usage_refuses_a_malformed_id`: 400.
  - `test_usage_ignores_image_less_placements`: a crop override is not a use.
  - `test_usage_lists_a_format1_collection_member`.

- [ ] **Step 2:** run them and see them fail.
- [ ] **Step 3:** implement.
- [ ] **Step 4:** run `tests/test_image_usage.py tests/test_route_order.py tests/test_import_guard.py tests/test_store_api_baseline.py`, and see them pass.
- [ ] **Step 5:** commit: `feat(images): derived image usage and its route`.

### Task 6: Art recall dedupes by image

**Files:**
- Modify: `backend/src/grimoire/store/context/art.py` (`_record_candidates`, `_library_candidates`, `candidates`)
- Test: `backend/tests/test_context_art.py`

**Interfaces:**
- Consumes: `overlay.image_root(cid, rid, vid, name, base)` and `image_descriptions.object_id_in(d, name)` for records; the campaign/world library dir plus `object_id_in` for library rows.
- Produces:
  - A candidate row gains `"image_id"` when its placement resolves, and has no key otherwise.
  - `candidates` keeps the **first** row per `image_id`, in its existing tier order (cast, then location, then entities, then library), after its existing `(kind, id, name)` dedupe. Rows without an id are never deduped.
  - Handles and URLs of the kept row are unchanged.

- [ ] **Step 1: Write the failing tests.**
  - `test_one_picture_in_cast_and_library_is_offered_once_as_the_cast_handle`;
  - `test_legacy_rows_are_not_deduped`;
  - `test_dedupe_is_deterministic_across_calls`.
- [ ] **Step 2:** run them and see them fail.
- [ ] **Step 3:** implement.
- [ ] **Step 4:** run `tests/test_context_art.py tests/test_art_handles_persist.py`. Run `evals/` through `pytest backend -k evals` if it covers available-art, and see it pass.
- [ ] **Step 5:** commit: `feat(images): art recall offers one handle per picture`.

### Task 7: "Used in" panel (frontend)

**Files:**
- Modify: `frontend/src/api/types.ts` (`ImageUsage`, `ImageUsageEntry` types, matching Task 5's shapes exactly)
- Modify: `frontend/src/api/client.ts` (`getImageUsage(imageId: string): Promise<ImageUsage>` → `GET /api/images/${encodeURIComponent(imageId)}/usage`)
- Create: `frontend/src/components/ImageUsage.tsx`, `frontend/src/components/ImageUsage.test.tsx`
- Modify: `components/character/ArtTab.tsx`, `components/pc/PCArtTab.tsx`, `components/EntityEditor.tsx`, `components/WorldArtPanel.tsx`, `components/PostImagePicker.tsx` (render `<ImageUsage imageId={…}/>` beside each `ImageDescriptionField` when the row has an `image_id`)

**Interfaces:**
- `ImageUsage({ imageId }: { imageId: string })`:
  - Collapsed, it is a button labelled `Used in…` and fetches nothing.
  - On click it calls `api.getImageUsage` once and renders a `.side-section` with an `<h4>Used in</h4>` and one `chip` button per entry, grouped by bucket.
  - A chip navigates with `useNavigate` to the record's page where the app has one (read the route table in `src/App.tsx`). An entry with no page renders as a non-clickable `<span class="chip on">`.
  - The label is `<bucket>: <name or id>`.
  - Error: the existing `.field-hint` error style.
  - It registers no keyboard binding.

- [ ] **Step 1: Write the failing tests** (`ImageUsage.test.tsx`):
  - `stays collapsed and fetches nothing until opened`;
  - `lists every bucket's entries as chips after opening`;
  - `a chip with a page navigates there`;
  - `shows an error hint when the request fails`;
  - in `character/ArtTab.test.tsx` (or the nearest existing ArtTab test): `an image with an image_id shows the Used in control; a legacy image does not`.
- [ ] **Step 2:** run them (`npx vitest run src/components/ImageUsage.test.tsx`) and see them fail.
- [ ] **Step 3:** implement.
- [ ] **Step 4:** run them plus `npx tsc --noEmit -p .` and the touched components' existing tests, and see them pass. `make check-eslint` stays at baseline.
- [ ] **Step 5:** commit: `feat(images): a "Used in" panel beside every image description`.

### Task 8: Docs, spec amendments, gate

**Files:**
- Modify: `docs/superpowers/specs/2026-10-05-content-addressed-image-store-design.md` (add "### Stage-2 amendments" after the follow-up amendments: R1–R7, one bullet each)
- Modify: `docs/store-guarantees.md` (image section: descriptions are global on the object; subjects are scoped; R1/R2; delete strips and fork copies a scope; usage is derived)
- Modify: `CLAUDE.md` ("Images live in…" note: one sentence saying a picture's description and subject tags live on its object, with legacy sidecars read first until migration)
- Modify: module docstrings that now contradict the code: `image_descriptions` (top), `image_subjects` (top), `overlay.read_description`, `campaign_images.read_descriptions`, `staging.repoint_urls`'s subjects note
- Test: `backend/tests/test_docs_guard.py` (must stay green; add a check that the image section names `image_scopes` and `image_usage` and that both modules exist, in the guard's existing style)

- [ ] **Step 1:** write the docs-guard assertion and see it fail.
- [ ] **Step 2:** write the docs and amendments, then see the guard pass.
- [ ] **Step 3:** full gate: `make check PY=backend/.venv/bin/python`. Expect only the allowed root-only failure, ratchets at baseline and the frozen snapshot unchanged.
- [ ] **Step 4:** commit: `docs(images): stage-2 amendments and guarantees`.
