# Unified image surfaces implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task by task. Steps use checkbox (`- [ ]`) syntax for tracking. The user approved the spec and authorized native execution with “Proceed”.

**Goal:** Give Characters, PCs, Locations, Lore, Items, Groups, and Creatures one complete Art workflow, retaining Greetings' separate workflow.

**Architecture:** Add a scoped record-art facade over the existing assets, overlay, description, and promotion primitives. One frontend controller owns record-art loading, management, and conflict recovery; shared display components serve records, galleries, drawers, and explicitly opted-in Markdown. Entity rails remain, with Details and Art inside the selected record.

**Tech stack:** FastAPI, plain pydantic v1/v2 models, existing filesystem image store, React 18, React Router 6, TypeScript, Vitest, pytest. No new dependency or storage format.

**Spec:** [Unified image viewing and record art](../specs/2026-10-11-unified-image-surfaces-design.md).

**Status:** implementation underway through native execution; spec and plan adversarial gates approved on 2026-10-11. The full plan and cleanup clarification were reviewed at `f7956d606`; error-response assertions received a focused approval at `0ca7e2d5f`. Reviews used the supplied complete documents and source facts because reviewer-local readers were unavailable; they are not runtime verification. Base source is `f816921c8`. Task checkboxes below record completed implementation and verification.

## Global constraints

- The seven API kinds are `characters`, `pcs`, `locations`, `lore`, `items`, `groups`, and `creatures`, in both world and campaign scope.
- Greetings are outside this roster. Existing greeting rendering, subjects, tagging queue, localization, and character-copy bridge remain separate.
- Actors require a selected version; entities use internal `default` and reject a public version modifier.
- Entity Details and Art live inside `editor-body`; preserve record indexes, metadata sidebars, read-only content, and explicit prose Edit.
- Upload accepts PNG, JPEG, GIF, and WebP. Gallery tiles contain the image at nominal 160px; identity preview aspects are square for actors and 16:10 for entities.
- Focus remains a single 0-100 position. Viewer zoom ranges from fit to 400% of natural size; initial fit never enlarges above natural size, stepping never wraps.
- Keyboard bindings use `useHotkeys` with modal ownership. Art URLs use `?tab=art`, with actor `v` only; tab/version changes replace history.
- Preserve absent versus explicit-empty descriptions, shared versus legacy scope, immutable image identity, overlay tombstones, and journalled primary swaps.
- Named writes send `expected_v`; description saves also send `expected_description_rev`; primary swaps/reuse distinguish omitted expectation from explicit-null `expected_primary_v`.
- No new store format, migration, garbage collector, image identity, metadata precedence, collection manifest, or export/bundle format changes are required.
- Follow [CLAUDE.md](../../../CLAUDE.md), [CONTRIBUTING.md](../../../CONTRIBUTING.md), and [store guarantees](../../store-guarantees.md). Preserve explanatory comments and use module-scope imports, filesystem resolvers, atomic writes, and classified campaign mutators.
- Use isolated synthetic data and existing placeholder names. Never inspect the real library or regenerate frozen-campaign `home/`.
- Commit sound checkpoints liberally, including documentation and meaningful passing intermediate changes. Stage explicit files only; eventual integration will squash checkpoints. A failing test used during development is followed by a passing implementation before a product checkpoint.

## Review focus

These five risks have explicit tests in their owning tasks:

1. Different record placements editing one shared image, including a masking legacy caption: reveal both effective and shared states and refuse stale reconciliation (Task 3).
2. A world writer racing a campaign append, or garbage collection between source validation and publication: preserve existing images and publish an arrived object only (Tasks 2 and 4).
3. Browser Back, an app link, or actor-version change with an open dirty editor: cancelling preserves the URL, draft, and context (Tasks 8 and 9).
4. Extreme-aspect, tiny, animated, unavailable, or resized images in nested overlays: preserve fit, bounded gestures, correct Escape ownership, and focus restoration (Task 7).
5. A streamed Markdown paragraph changing structure while two collection occurrences are open: preserve occurrence selection and greeting opt-out (Task 13).

## File structure and shared interfaces

The backend `store/record_art/` package is a facade, with `targets.py` owning kind/scope validation and safe addressing, `read.py` owning uniform metadata/provenance, and `write.py` owning append/reuse/guarded mutations. It imports existing store modules; those modules do not import this facade. Description revision/CAS stays in `image_descriptions.py`, which already owns metadata precedence.

`routes/record_art.py` adapts HTTP to the facade; register it before generic world/entity routes. Existing actor mutation URLs call the same guarded facade: world characters live in `routes/characters.py`, world PCs in `routes/worlds.py`, and both campaign actor kinds in `routes/campaigns.py`. Entity mutations live in `routes/entities.py`. Existing image-serving helpers own download behavior.

Frontend server DTOs live in `api/types.ts`, server calls in `api/client.ts`. Under `components/art/`, create `types.ts`, `recordArtAdapter.ts`, `useRecordArt.ts`, `ImageTile.tsx`, `ImageViewer.tsx`, `viewerGeometry.ts`, `RecordArtPanel.tsx`, `ExistingArtPicker.tsx`, `RecordArtEntry.tsx`, and `ArtNavigationProvider.tsx`. Each component/hook has an adjacent test. Character and PC Art wrappers retain only addressing and specialized related sections.

Use these names consistently across tasks:

```ts
type ArtRecordKind = "characters" | "pcs" | EntityKind;
type RecordArtTarget =
  | { scope: EntityScope; kind: "characters" | "pcs"; record_id: string; version: string }
  | { scope: EntityScope; kind: EntityKind; record_id: string; version?: never };
type ArtSourceRef =
  | { surface: "record"; target: RecordArtTarget; name: string; expected_v: string }
  | { surface: "library"; scope: EntityScope; name: string; expected_v: string };
type DescriptionState = {
  scope: "shared" | "legacy" | null; text: string; described: boolean;
  conflicts: { text: string; from: string }[];
};
type ArtProvenance = "world" | "campaign" | "inherited" | "world-original";
type ArtCreated = { name: string; ext: string; v: string; image_id: string };
type ArtLinkResult =
  | { status: "ok"; created: ArtCreated; no_op?: boolean }
  | { status: "partial"; created: ArtCreated; error: { kind: string; detail: string } };
type ArtOperation = "append" | "reuse" | "promote" | "focus" | "description" | "remove" | "draft_description";
type DescriptionSaveResult =
  | { status: "ok" }
  | { status: "partial"; kind: "description_cleanup_incomplete";
      committed: true; confirmed_text: string; recovery: { available: false } | {
        available: true; description_rev: string; effective_description: DescriptionState;
        write_target_description: DescriptionState;
      } };
```

`RecordArtImage` preserves `name`, `ext`, `v`, optional `image_id`, `url`, and `thumb`, and adds `description`, `described`, optional `description_scope` (`shared`/`legacy`), `description_rev: string | null`, optional `effective_description: DescriptionState`, optional `write_target_description: DescriptionState`, optional `focus`/dimensions, `provenance`, and a typed `placement`. `RecordArtRead` has `target`, `images`, `world_originals`, and `operations: ArtOperation[]`. Every kind supports the six common operations; `draft_description` is offered only through existing world endpoints. Related actor sections can be assembled by wrappers from existing reads. Unreadable caption metadata has `description_available: false` and a null revision with no invented shared/effective state; that row remains viewable but cannot authorize a guarded caption edit.

A placement's target scope addresses the **effective read context**; provenance separately identifies inheritance. An inherited picker candidate therefore carries campaign addressing and is revalidated through that overlay. World-original descriptors are read-only and served from world URLs. Do not use their physical origin as permission to bypass the effective picker scope.

`ImageDescriptor` is a display-only type in `components/art/types.ts`: stable `key`, `originalUrl`, `thumbnailUrl`, human `label`/`alt`, byte `version`, optional download/record destinations, image id/dimensions/description state, role, and provenance. It carries no mutation callback. DTO-to-display mapping is explicit.

## Verification commands

Use PowerShell from this worktree. Backend commands below use the main checkout interpreter but **this worktree's** sources through Makefile:

```powershell
make test-py-fast PY=C:/Users/charl/github/grimoire/backend/.venv/Scripts/python.exe TESTS="backend/tests/test_record_art_routes.py"
cd frontend
npm exec vitest run src/components/art/ImageViewer.test.tsx
cd ..
make check PY=C:/Users/charl/github/grimoire/backend/.venv/Scripts/python.exe
```

The first command is a focused development check, not the gate. Install frontend dependencies in this worktree with `npm --prefix frontend ci` before frontend tests; the worktree currently has no `node_modules`. Do not use bare worktree `pytest`, whose editable install points at the other checkout. Run pydantic compatibility through the gate. Browser and Android verification are Task 14; source tests cannot establish gesture or layout correctness.

## Implementation tasks

### Task 1: Uniform seven-source reads and provenance

**Files:** Create `backend/src/grimoire/store/record_art/{__init__,targets,read}.py`, `backend/src/grimoire/routes/record_art.py`, and `backend/tests/test_record_art_routes.py`; modify `backend/src/grimoire/store/__init__.py`, `backend/src/grimoire/routes/__init__.py`, and the gallery reads in `backend/src/grimoire/routes/characters.py`.

**Interfaces:** Export `ART_RECORD_KINDS = ("characters", pcs.ASSET_BASE, *entities.ENTITY_KINDS)`, `read_world(wid: str, kind: str, rid: str, version: str | None) -> dict`, and `read_campaign(cid: str, kind: str, rid: str, version: str | None) -> dict`. HTTP is `GET /api/{worlds/wid|campaigns/cid}/record-art/{kind}/{rid}`, actor `version` query. Return the shared DTO above; description revision fields arrive in Task 3.

- [x] Write `test_record_art_read_matrix`, parameterized over the seven kinds and two scopes, using the synthetic surface setup pattern in `test_image_surfaces.py`. Assert primary/gallery/other addressable names, bytes token, correct URL, optional image id, and complete common operations. Add `test_record_art_rejects_greetings_and_bad_versions` asserting 404 for greeting/unknown kind/missing record/version and rejection of an entity version modifier.

  ```python
  assert response.status_code == 200
  assert body["target"]["kind"] == kind
  assert {"append", "reuse", "promote", "focus", "description", "remove"} <= set(body["operations"])
  ```

- [x] Run `make test-py-fast PY=C:/Users/charl/github/grimoire/backend/.venv/Scripts/python.exe TESTS="backend/tests/test_record_art_routes.py"`; expect failure for the missing uniform routes.
- [x] Implement safe target resolution using current actor-version/entity resolvers and `overlay.view`. Read campaign art through the overlay, including detached/tombstoned/shadowed cases; do not make another inheritance algorithm. Return primary focus and cheap object dimensions without decoding legacy files. Add provenance and typed placement references to existing gallery rows without removing existing fields. Read-only world originals use world URLs.
- [x] Add `test_world_original_is_not_campaign_override`, `test_detached_and_tombstoned_art_are_absent`, and `test_optional_related_failure_does_not_erase_effective_art`. Run the focused file plus `backend/tests/test_image_surfaces.py backend/tests/test_image_descriptions_overlay.py`; expect all selected tests to pass. Commit the working uniform-read checkpoint.

  Verified: 132 selected tests passed, including import, overlay, facade, and route-order guards; `check-lint` passes. `check-mypy` reports above-baseline findings only in untouched `atomic.py` and `proclock.py`; investigate platform baseline during the full gate.

### Task 2: Server-allocated append without overwrites

**Files:** Create `backend/src/grimoire/store/record_art/write.py` and `backend/tests/test_record_art_writes.py`; modify the new route/facade, `backend/src/grimoire/store/locks.py`, and `backend/src/grimoire/store/assets.py`.

**Interfaces:** `append_world(wid, kind, rid, version, data: bytes, ext: str) -> dict` and `append_campaign(cid, kind, rid, version, data: bytes, ext: str) -> dict`. Add `locks.image_name_locks_for(keyed: Iterable[tuple[Path, str]]) -> list[StripeLock]` and `assets.name_locks_held(*keyed: tuple[Path, str])` for one sorted/deduplicated set across directories. POST uniform `.../images` takes multipart `file` and returns `{name, ext, v, image_id}`.

- [x] Write `test_append_matrix_preserves_existing_primary` and `test_append_first_success_uses_empty_primary`. Pin avatar-first only when effective primary and unresolved local primary are unoccupied; numeric append is maximum occupied `gallery_N` plus one, counting unresolved local placements. Tombstones/detachment determine which world slots are effective; deleted gaps cannot overwrite a current image.

  ```python
  assert first.json()["name"] == "avatar"
  assert after_gallery_9.json()["name"] == "gallery_10"
  assert primary_after["image_id"] == primary_before["image_id"]
  ```

- [x] Run `make test-py-fast PY=C:/Users/charl/github/grimoire/backend/.venv/Scripts/python.exe TESTS="backend/tests/test_record_art_writes.py"`; expect missing append behavior to fail.
- [x] Implement append with the existing byte-type sniffing and sanitization. Prepare expensive work outside final holds where possible. Campaign writers take `campaign_lock`; choose a candidate, acquire the complete world/campaign name-stripe set, recheck occupancy, and retry another candidate after releasing a losing set. Hold necessary sidecars before ingest/GC exclusion and object locks; publish only an intact arrived object. If a prepared object disappeared before publication, safely re-establish it under ingest exclusion or refuse visibly. Classify public campaign mutators with reasons; do not extend the frozen unreviewed backlog.
- [x] Add barrier-driven `test_two_appends_get_distinct_slots`, `test_world_append_racing_campaign_candidate_does_not_get_shadowed`, and `test_gc_cannot_collect_between_append_validation_and_reference`. Use barriers/events, never timing sleeps. Assert existing object identities and descriptions survive; invalid file/missing destination writes nothing.
- [x] Run the focused file plus `backend/tests/test_image_name_locks.py backend/tests/test_locks_store.py backend/tests/test_import_guard.py backend/tests/test_image_gc.py`; expect all selected tests to pass. Extend upload-route coverage to recognize the new POST facade as reaching the existing fourteen record surfaces, without inventing new storage surfaces. Commit append and its race tests; commit any subsequent passing hardening separately.

Verified: 333 selected tests passed, including campaign publication, sorted name locks, GC, upload roster, and route ordering. check-lint passes. The existing GC case-variant fixture now runs on Windows.

### Task 3: Guard named mutations and shared-description conflicts

**Files:** Modify `store/record_art/{read,write}.py`, `store/image_descriptions.py`, `store/image_store.py`, `routes/record_art.py`, `routes/characters.py`, `routes/worlds.py`, `routes/campaigns.py`, `routes/entities.py`, `routes/common.py`, and entity summary assembly in `routes/entities.py`; create `backend/tests/test_record_art_preconditions.py` and `backend/tests/test_record_art_description_guards.py`.

**Interfaces:** World/campaign guarded mutation functions take `(scope_id, kind, rid, version, name, operation, *, expected_v=None, expected_primary_v=None, check_primary=False, expected_description_rev=None, description=None, focus=None, cleanup_only=False) -> dict`; operation is `delete`, `promote`, `description`, or `focus`. Add `image_descriptions.description_state_in(...) -> dict` and optional `expected_description_rev`/`cleanup_only` keywords to the description write path. Return one coherent description snapshot and digest through Task 1 reads. Existing URLs and owning route modules stay. Add entity `.../images/avatar/focus` and optional `image_focus` summaries. A post-commit cleanup failure remains HTTP 500 with structured detail matching the partial branch of `DescriptionSaveResult`; no pre-commit failure is labelled committed.

- [x] Write `test_named_mutations_matrix_rejects_replaced_image`, `test_primary_expectation_distinguishes_absent_from_omitted`, and `test_inherited_focus_changes_campaign_only`. Assert 409 `stale_image` before side effects, backward compatibility for omitted old preconditions, and focus bounds 0-100. Promotion must retain both images and journal recovery; removal must tombstone inherited visibility and leave blobs/other placements intact.

  Byte/primary checkpoint: 64 new matrix cases passed; the eleven-route entity-kind roster passed after adding entity focus. Lint passed with ten findings removed (baseline 1136). The broader compatibility selection is still running; Windows mypy findings remain confined to untouched atomic.py/proclock.py. Caption CAS and cleanup recovery continue in a separate focused test file so this checkpoint contains only implemented behavior.
- [x] Write `test_cross_placement_description_save_conflicts`, covering text/text, text/explicit-empty, and two first descriptions. Assert byte token stays unchanged, first metadata survives, and second Save returns `stale_description` with current state. Add `test_legacy_masked_conflict_exposes_shared_write_target`, then change the shared value again between reconciliation and retry and require another 409.

  ```python
  assert stale.status_code == 409
  assert stale.json()["kind"] == "stale_description"
  assert stale.json()["write_target_description"]["text"] == "New shared description"
  assert image_after["v"] == image_before["v"]
  ```

- [x] Run `make test-py-fast PY=C:/Users/charl/github/grimoire/backend/.venv/Scripts/python.exe TESTS="backend/tests/test_record_art_preconditions.py"`; expect missing guarded writes/revisions to fail.
- [x] Implement checks and named operations under one up-front name/sidecar lock set, and campaign lock where applicable. Calculate a deterministic digest over image binding, relevant legacy states, underlying shared description/conflicts, and presence/empty/text distinctions. Exclude focus/subjects from that digest. Compare fresh metadata under the object lock and update under that same hold; the `image_store.update` callback remains a pure dict edit. Preserve legacy fallback and clear keys only after a confirmed object write. Reads pair displayed states and revision from one snapshot; unreadable write-target metadata must not become invented empty state authorizing Save.
- [x] Make `image_store.update` read authoritative metadata with `read_fresh` under its object stripe, so its callback cannot mutate an older cached raw projection after the new description guard checked fresh state. Add `test_description_guard_uses_fresh_object_state_not_cached_projection`, asserting stale cached description is refused and unrelated newer metadata survives.
- [x] Extend plain request models with optional guards. Add a v1/v2-compatible field-presence helper in `routes/common.py` so explicit-null primary expectation is distinguishable from omission; do not use `Field`, validators, `ConfigDict`, or direct `model_dump`. Map description conflicts to 409 with both effective and masked shared states; preserve existing busy/unavailable errors. Refresh focus summary tokens without changing original byte tokens.
- [x] Add `test_shared_commit_then_legacy_cleanup_failure_is_partial` by injecting a write/unlink failure after `image_store.update` confirms. Assert the HTTP 500 JSON body has top-level `kind == "description_cleanup_incomplete"`, `committed is True`, and readable current effective/shared states plus revision. An unavailable strict recovery read must return `recovery.available == False`, never a synthetic empty caption or revision. Detect the confirmed object step in the store and preserve legacy fallback/cleanup ordering; do not roll back that shared save.
- [x] Add `test_cleanup_only_retry_never_rewrites_shared_description` and `test_shared_change_before_cleanup_retry_is_refused`. The existing PUT's optional `cleanup_only=true` requires both guards, checks current shared text/revision against the confirmed saved state under the same holds, and clears only applicable legacy keys. Repeated cleanup failure remains partial; another writer causes 409 reconciliation. A clean idempotent state discovered by fresh read needs no automatic Save.
- [x] Run the focused file plus `backend/tests/test_image_descriptions_store.py backend/tests/test_image_descriptions_overlay.py backend/tests/test_image_description_routes.py backend/tests/test_image_store.py backend/tests/test_assets_store.py backend/tests/test_locks_http.py`; expect all selected tests to pass. Commit byte/primary guards, independently passing description CAS, and cleanup recovery as separate checkpoints when each is sound.

Task 3 complete: 426 storage/overlay/description/lock/import cases passed, one platform skip; final uniform-read/crop/error selection passed 41 cases. The 15 caption cases cover masked shared state, fresh metadata, two cooperating writers, unreadable recovery, inherited legacy fallback, and cleanup-only guards. Lint passes at 1136. Existing missing-image errors retain their established text. The timestamp no-op test now compares with the timestamp the filesystem stored instead of assuming one-nanosecond resolution. No baseline was increased.

### Task 4: Scoped reuse, including partial primary failure

**Files:** Modify `store/record_art/{targets,write}.py` and `routes/record_art.py`; create `backend/tests/test_record_art_reuse.py`.

**Interfaces:** `reuse_world(wid, kind, rid, version, source: dict, intent: str, *, expected_primary_v=None, check_primary=False) -> dict` and campaign counterpart. POST uniform `.../images/link` consumes `ArtSourceRef`, intent `gallery`/`primary`, and the destination primary expectation for primary intent. Successful responses identify the destination slot. An append that landed before failed promotion returns a typed result `{status: "partial", created: {name, ext, v, image_id}, error: ...}`; never claim a failed request made no change.

- [x] Write `test_reuse_cross_kind_matrix_and_library_sources`: every destination kind/two scopes, every source kind, both libraries where visible. Assert same object identity, no extra blob, centered focus, preserved old primary on primary intent, and a no-op for unchanged already-primary reuse.
- [x] Write `test_reuse_rejects_greeting_hidden_foreign_changed_and_unarrived_sources`; assert no destination publication. Explicitly include another campaign in the same world, another world, detached records, tombstones, shadowed world originals, missing/corrupt objects, and source version deletion.

  ```python
  assert linked["created"]["image_id"] == source_image["image_id"]
  assert partial["status"] == "partial"
  assert partial["created"]["name"] == "gallery_1"
  assert destination_after_refusal == destination_before_refusal
  ```

- [x] Run `make test-py-fast PY=C:/Users/charl/github/grimoire/backend/.venv/Scripts/python.exe TESTS="backend/tests/test_record_art_reuse.py"`; expect missing link behavior to fail.
- [x] Resolve source by its visible placement, never arbitrary object id/path. Revalidate source token, destination occupancy/primary expectation, and record/version existence under complete sorted source/destination name holds. Campaign sources use their effective overlay context. Hold ingest/GC exclusion through intact-arrival check and reference publication; adopt a legacy source without changing/deleting its file and carry its caption using legacy fallback. Append then use existing journalled promotion; partial failure reports the created slot for promotion-only recovery.
- [x] Add `test_reuse_source_change_during_legacy_ingest_is_refused`, `test_reuse_gc_race_publishes_no_dangling_ref`, and `test_reuse_primary_partial_result_is_retryable_without_second_append`. Run the focused file plus `backend/tests/test_image_migration_run.py backend/tests/test_campaign_images_store.py`; expect pass. Commit reusable linking, then any passing partial-failure correction separately.

Task 4 complete: all 35 reuse cases passed before the existing wildcard-filename fixture stopped the broader run at 135 passes. After making that fixture representable on Windows, all 192 remaining library/lock/import/route-order cases passed. The new empty-slot caption assertion passed separately; lint remains 1136. Reuse retains legacy source files, carries reviewed-empty captions, makes no extra blob for arrived sources, refuses hidden/shadowed/foreign/detached sources, protects publication from GC, and returns the created slot after failed promotion.

### Task 5: Local downloads with cache/header separation

**Files:** Modify `backend/src/grimoire/routes/common.py`; create `backend/tests/test_image_downloads.py`; extend named serving routes only where the existing shared helper needs a human filename hint.

**Interfaces:** Existing image-serving URLs accept `download=1`. `_serve_image_file` returns original retained/sanitized bytes with attachment disposition and a safe slot/record filename; ordinary inline/thumb reads keep their established validators and cache behavior.

- [x] Write `test_download_attachment_does_not_change_inline_headers`, `test_download_ignores_thumbnail_width`, `test_download_filename_handles_reserved_characters`, and `test_download_refuses_missing_or_corrupt_blob`. Assert normal image reads do not inherit attachment headers, stale tokens retain their existing cache behavior, and no pre-ingest file is claimed.

  ```python
  assert attachment.headers["content-disposition"].startswith("attachment;")
  assert "attachment" not in inline.headers.get("content-disposition", "")
  assert attachment.content == stored_sanitized_bytes
  ```

- [x] Run `make test-py-fast PY=C:/Users/charl/github/grimoire/backend/.venv/Scripts/python.exe TESTS="backend/tests/test_image_downloads.py"`; expect missing attachment behavior to fail.
- [x] Implement attachment branch in the shared serving helper with separate URL/cache semantics, safe encoded filename, retained original representation, and intact-blob validation. Remote Markdown sources gain no backend download/proxy path.
- [x] Run the focused file plus `backend/tests/test_thumbs.py backend/tests/test_serving_compression.py`; expect pass. Commit the download checkpoint.

Task 5 complete: attachment, thumbnail, and serving selection passed 147 cases with one platform skip; eight filename/extension sanitization cases passed after the final hardening. Every record kind/world/campaign route uses the same original attachment branch; inline validators remain separate, stale tokens still revalidate, and download ignores thumbnail width. Lint passes at 1136.

### Task 6: Exhaustive frontend contract and scoped adapter

**Files:** Modify `frontend/src/api/types.ts`, `api/client.ts`, `api/client.test.ts`, and `appEvents.ts`; create `components/art/{types,recordArtAdapter}.ts` and `recordArtAdapter.test.ts`.

**Interfaces:** Implement the DTOs declared above, `createRecordArtAdapter(target: RecordArtTarget): RecordArtAdapter`, and `toImageDescriptor(image: RecordArtImage): ImageDescriptor`. The adapter exposes `read({fresh,signal})`, `append(file)`, `reuse(source,intent,expectedPrimary)`, `remove(image)`, `promote(image,expectedPrimary)`, `setFocus(image,focus)`, `setDescription(image,text,expectedDescriptionRev): Promise<DescriptionSaveResult>`, and `finishDescription(image,text,expectedDescriptionRev): Promise<DescriptionSaveResult>`. The latter sends `cleanup_only=true`, never an ordinary shared Save. Other methods return promises of their corresponding HTTP results. `draftDescription(image): Promise<string>` exists only for a world target with an existing draft endpoint. Add `artChanged`/`onArtChanged` to the existing event-channel pattern for landed mutations, including shared description edits.

- [x] Write `test.each` over all seven kinds/two scopes asserting actor/entity addressing, encoded segments, version query, source context, and required precondition payloads. Pin null primary expectation as an explicitly present property; reject greeting kinds at the type/runtime boundary. Add a client test showing a fresh post-write art GET retires an earlier in-flight GET.

  ```ts
  expect(body).toHaveProperty("expected_primary_v", null);
  expect(body.expected_description_rev).toBe(capturedDescriptionRevision);
  expect(campaignAdapter.draftDescription).toBeUndefined();
  ```

- [x] Run `npm --prefix frontend test -- src/components/art/recordArtAdapter.test.ts src/api/client.test.ts`; expect missing calls/adapter to fail.
- [x] Implement one adapter with exhaustive kind handling and common capabilities; add focused API methods to the existing client rather than exporting a second request stack. Preserve old client signatures with optional added guards. Emit changes for confirmed saves and partial writes, including HTTP 500 carrying `description_cleanup_incomplete`/`committed=true`; rejected pre-write requests emit nothing. The adapter converts only that structured confirmed partial failure into `DescriptionSaveResult`. Other description errors remain failures. Build download URLs from known local serving descriptors and `URLSearchParams`; never infer record ownership from URLs.
- [x] Add `test_confirmed_description_partial_error_invalidates_art` and `test_stale_description_error_does_not_emit_save_event`. Preserve the original failure behavior for existing standalone callers while giving new guarded callers typed recovery; no generic 500 is treated as successful or replayable.

  The existing `main.py` HTTPException handler emits dict detail as the top-level
  JSON body. Keep `detail` a human message string, with `kind`, `committed`,
  `confirmed_text`, recovery states, and revisions alongside it. The client reads
  these fields from `ApiError.kind`/`ApiError.body`, not from `ApiError.detail`.
- [x] Assert `reuse` yields `status === "partial"` with `created.name`, and that campaign adapters have no draft endpoint. Run the focused tests and `npm --prefix frontend run typecheck`; expect pass. Commit the DTO/client checkpoint and adapter separately if each is already sound.

Verification: 21 adapter tests plus 158 existing client tests passed; typecheck and ESLint ratchet passed (727 findings, all at baseline).

### Task 7: Contained tiles and accessible image viewer

**Files:** Create `components/art/{ImageTile,ImageViewer}.tsx`, `viewerGeometry.ts`, and adjacent tests; modify `frontend/src/index.css`. Add `components/art/useImageViewer.ts` and its test for controlled sequence lifecycle.

**Interfaces:** `ImageTile({image: ImageDescriptor,onOpen: (opener: HTMLElement) => void})`; `ImageViewer({images,initialKey,opener,fallbackFocus,onClose,onOpenRecord?})`. `useImageViewer` exposes `open(images,key,opener)`, `close()`, and viewer props, closing when its owning context disappears. Geometry exports `fitScale(natural,viewport)`, `clampPan(natural,viewport,scale,pan)`, and `focusWindow(natural,targetAspect,focus)`; zoom values are natural-size scale factors, not percentages relative to fit.

- [x] Write `ImageTile` tests for portrait/map/narrow/tiny art, meaningful labels, optional role/provenance badges, lazy thumbnail loading, and unavailable treatment. In geometry tests pin:

  ```ts
  expect(fitScale({width: 100, height: 50}, {width: 800, height: 600})).toBe(1);
  expect(fitScale({width: 1600, height: 800}, {width: 400, height: 400})).toBe(0.25);
  ```

  Cover clamping on both axes, viewport resizing, and zero-size measurements without nonfinite transforms. Use existing pointer/viewport testkit patterns.
- [x] Write viewer tests for placement keys versus shared object ids, primary/numeric/other order, one-image controls, nonwrapping bounds, natural 100% and maximum 400%, reset on step/token change, missing original Retry, removed selection close, and active-original-only loading. Test drag and two-pointer pinch without treating drag as stepping.
- [x] Run `npm --prefix frontend test -- src/components/art/ImageTile.test.tsx src/components/art/ImageViewer.test.tsx src/components/art/viewerGeometry.test.ts src/components/art/useImageViewer.test.ts`; expect missing viewer behavior to fail.
- [x] Implement contained tiles with bounded fallback dimensions and responsive width, using existing versioned thumbnail serving rather than assuming cover-crop srcset widths equal rendered contained widths. Implement a portal modal with initial focus, focus containment, background inertness, saved scroll styles, and opener/fallback restoration. Put Escape/step/zoom/fit bindings in `useHotkeys`, mirroring disabled buttons and excluding editable/control-owned keys. Preserve originals/animation; load only active original; maintain per-image load generation so an old image's load/error cannot affect a new selection.
- [x] Add `test_viewer_escape_preserves_underlying_drawer`, `test_failed_retry_does_not_substitute_image`, and `test_tiny_image_is_not_upscaled_on_fit`. Verify Open original, local Download, optional Open record, collapsible phone information, and on-demand `ImageUsage`. Run focused tests plus `src/shortcuts/useHotkeys.test.tsx`; expect pass. Commit tiles, tested geometry, and the working modal as separate passing checkpoints.

Verification: 36 viewer, tile, geometry, lifecycle, and shortcut tests passed; typecheck passed. Pointer tests use the existing testkit.

### Task 8: Typed addresses and guarded navigation

**Files:** Modify `frontend/src/worldPaths.ts`, `worldPaths.test.ts`, `main.tsx`, and `App.tsx`; create `components/art/ArtNavigationProvider.tsx`, its test, and `frontend/src/router.tsx`/`router.test.tsx`.

**Interfaces:** Extend `SectionTarget` with actor records (`characters`/`pcs`, optional `v` and `tab: "art"`) and entity records (optional `tab: "art"`, no `v`); greeting records accept neither Art modifier. Add `parseRecordModifiers(section,search)` for supported version/tab modifiers. `ArtNavigationProvider` exposes `registerDraftGuard(guard: () => boolean): () => void` and `requestArtTransition(action: () => void): void` for local selection/editor transitions. Guards return true only when leaving is permitted; a single provider owns route blocking.

- [x] Write path round-trip tests for all seven kinds/two scopes, special characters, actor version, and illegal greeting/entity modifiers. Test tab/version changes replace history and gallery-to-record navigation pushes one entry.
- [x] Write data-router tests that open a dirty description/crop, attempt an app Link and browser-equivalent Back, cancel discard, and retain both URL and form. On confirmed discard, navigation proceeds once; a clean panel and an already-saved upload are not blocked.

  ```ts
  expect(router.state.location.pathname).toBe(recordPath);
  expect(router.state.location.search).toContain("tab=art");
  expect(screen.getByRole("textbox")).toHaveValue("Unsaved description");
  ```

- [x] Run `npm --prefix frontend test -- src/worldPaths.test.ts src/router.test.tsx src/components/art/ArtNavigationProvider.test.tsx`; expect missing modifiers/blocking to fail.
- [x] Use React Router's installed data-router API to enable `useBlocker`: replace only the `BrowserRouter` host with `RouterProvider` and a single wildcard route rendering the existing `App`/descendant `Routes`. Create the browser router once outside rendering. Keep `RunRegistryProvider` above the router and preserve every existing route/redirect. Mount one Art navigation provider around the routed app; scoped record switches/version/tab handlers request its guard before changing local state. A store-root change still forces the existing safety remount instead of carrying a draft into another library.
- [x] Add `test_navigation_keeps_run_registry_and_existing_route_behavior`, plus Art modifier canonicalization tests in `WorldView` so path normalization retains supported query values. Run focused tests and existing App/WorldView route tests plus typecheck; expect pass. Commit URL changes and the independently passing navigation-host change separately. Do not add global keyboard or click-interception workarounds.

Verification: 36 path/router/guard tests, 130 existing App/WorldView cases, two new canonicalization cases, and 24 adapter/router cases passed. Typecheck and focused lint passed.

### Task 9: One record-art controller, panel, picker, and draft recovery

**Files:** Create `components/art/{useRecordArt,RecordArtPanel,ExistingArtPicker,RecordArtEntry,DescriptionConflictPanel}` (`.ts` for the hook, `.tsx` for components) and adjacent tests; modify `ImageDescriptionField.tsx`/its test, `AvatarFocusPicker.tsx`/its test, `MobileArtEntry.tsx`/its test, and `index.css`.

**Interfaces:** `useRecordArt(target: RecordArtTarget,{onSaved: () => Promise<void>}): RecordArtController`; `RecordArtPanel({controller,related?})`; `RecordArtEntry({controller,name})`; `ExistingArtPicker({adapter,expectedPrimary,onCreated,onClose})`. Controller owns target generation, loading/empty/ready/failed state, selection, browse/manage mode, mutation progress/results, and at most one description/crop draft. A description draft contains captured image/revision and effective/write-target states; a crop draft captures focus and image token. Add optional controlled editor props to `ImageDescriptionField` so the controller owns panel drafts while existing library callers retain their standalone behavior.

`AvatarFocusPicker` gains optional `aspect` (default 1), human `label`, busy/error state, and a guarded Cancel. Its crop window uses Task 7 geometry: image aspect greater than target crops horizontally, otherwise vertically. `MobileArtEntry` accepts separate optional preview-open action while View art opens the panel; an empty preview opens Art browsing, where Add images enters management and opens the chooser.

- [x] Write `RecordArtPanel.test.tsx` using a seven-kind/two-scope `test.each` matrix. For **each** combination exercise upload, reuse, primary promotion, focus, text/empty description Save, usage, and removal. Pin browse-only initial mode, zero Art entry, unknown/failed count, mixed provenance, read-only world originals, gallery-only fallback, numeric ordering, and explicit Manage/Done behavior.
- [x] Write controller tests using deferred reads/writes: changing target drops old reads/results/banners, pending operations disable conflicts, a successful write followed by failed refresh reports exactly **Saved, but the gallery could not be refreshed**, and an ambiguous disconnect triggers fresh read without replay. Batch upload tries every file sequentially, reports each failure, and retries only deliberately selected failed files.

  ```ts
  expect(screen.getByText("Saved, but the gallery could not be refreshed")).toBeVisible();
  expect(adapter.append).toHaveBeenCalledTimes(3); // three selected files, one refused
  expect(adapter.promote).toHaveBeenCalledWith(createdSlot, capturedPrimary);
  ```

- [x] Run `npm --prefix frontend test -- src/components/art/useRecordArt.test.ts src/components/art/RecordArtPanel.test.tsx src/components/art/ExistingArtPicker.test.tsx`; expect missing shared management to fail.
- [x] Implement the controller with Task 6's adapter and Task 8's draft guard. Capture immutable baselines when editing, not refreshed row values when Save is clicked. Shared-save events re-read metadata with fresh GETs without replacing unsaved text or rebasing revisions. Done is an exit, never a rollback. Description/crop Save and Cancel are explicit. Only one editor opens; a refused discard preserves it. Viewer opening also uses that guard. Promote/delete confirmations explain named narrative links and placement-only removal.
- [x] Implement the picker from visible world/effective campaign gallery rows with local kind/text filters, excluding greetings and read-only originals. Preserve the destination primary expectation captured at open. A typed partial reuse result refreshes and offers promotion on the created slot; it never retries append. Other errors preserve deliberate selection/draft and show appropriate Retry/Reload behavior.
- [x] Implement `DescriptionConflictPanel` to show current effective and shared write-target state plus draft. Explicit choices use the shared value, keep/revise the draft, or cancel; only a deliberate reconciliation updates expected revision. A later conflict repeats this flow. Draft-generation endpoints run only on explicit request, world-only, and populate unsaved text. Read-only viewing/usage/picker sends no model request.
- [x] Implement partial-description recovery separately from stale-before-write conflict: show **The shared description saved, but the local override could not be cleared**, refresh/notify other uses, and retain the editor. Preserve `confirmed_text` independently from the still-editable draft. With displayed readable recovery metadata offer **Finish applying saved description** calling `finishDescription` with that confirmed text; with unavailable metadata offer fresh Reload only. A concurrent shared edit returns to explicit reconciliation. Neither refresh nor Retry automatically rewrites a shared description.
- [x] Add `test_partial_description_save_refreshes_other_uses_and_keeps_editor`, `test_finish_description_only_clears_legacy_override`, and `test_unavailable_recovery_cannot_authorize_cleanup`. Assert the confirmed shared edit is displayed elsewhere even though Save received HTTP 500, and a subsequent shared writer is preserved during retry.
- [x] Add `test_masked_shared_value_is_visible_before_rebase`, `test_invalidation_preserves_original_draft_revision`, and `test_reuse_partial_retry_promotes_created_slot_only`. Test square versus 16:10 crop geometry, focus 0/50/100, and no pixel mutation; retain standalone description and crop callers. Run focused tests, description/crop/mobile tests, and typecheck; expect pass. Commit passing controller, panel/editor, and picker checkpoints separately.

Verification: 84 shared controller/panel/picker/viewer/editor/mobile/geometry/shortcut tests passed; 14 navigation/controller/router regression cases passed. Final typecheck passed; full ESLint was 727 at baseline and final changed-file lint passed. Real client recovery test proves other-use invalidation and preservation of a later shared writer.

### Task 10: Character and PC integration, greeting bridge retained

**Files:** Modify `routes/CharacterPage.tsx`, `routes/PCPage.tsx`, `components/character/ArtTab.tsx`, `components/pc/PCArtTab.tsx`, their existing tests, and `components/character/shared.tsx` if its primary preview helper needs focus/entry wiring. Extract existing related art into `components/character/CharacterArtRelated.tsx` with its own test.

**Interfaces:** Both actor pages create one `useRecordArt` controller for their resolved selected version and pass it to their Art wrapper/entry. `CharacterArtRelated({scope,record,version,onCopied})` owns existing Appears in/greeting copy, other-world-version, and localization integration; copied destination refreshes the shared controller. Specialized read-only version art uses the shared viewer; greeting artwork keeps its existing separate viewing contract.

- [x] Write actor route tests for `?tab=art&v=...`, phone empty/gallery-only entry, initial unknown count, failed art list with readable record prose, and version change closing viewer/management. Assert the identity artwork opens the viewer and crop is available only through Manage.

  ```ts
  expect(screen.getByRole("dialog", {name: "Image viewer"})).toBeVisible();
  expect(screen.queryByRole("dialog", {name: /crop/i})).not.toBeInTheDocument();
  expect(screen.getByRole("button", {name: "View art"})).toBeVisible();
  ```

- [x] Run `npm --prefix frontend test -- src/routes/CharacterPage.test.tsx src/routes/PCPage.test.tsx src/components/character/ArtTab.test.tsx`; expect the old shelf/portrait behavior to fail new assertions.
- [x] Replace generic shelf/mutation handling with the shared panel/controller, remove client-count-based ordinary upload naming, and keep selected actor version throughout reads/saves/links. Header/mobile entry and Art count use the same read; no independent second shelf request/state machine. Wire fresh detail/summary refresh to `onSaved` and `artChanged`.
- [x] Preserve Appears in subjects, greeting links, copyability restrictions, greeting copy/localization handlers, and other-version addressing. New common panel never receives greeting mutation targets. Add `test_greeting_copy_becomes_normal_character_placement` and regression assertions that viewing/tagging the source still uses the separate greeting behavior. Run focused tests plus `src/components/character/CharacterArtRelated.test.tsx`; expect pass. Commit Character and PC integrations as separate passing checkpoints after shared support exists.

Character checkpoint: 99 route/bridge/wrapper/controller cases passed; TypeScript passed; repository ESLint recorded 727 findings at its existing baseline. Character Art uses one controller, server allocation, guarded promotion, common viewing and separate greeting borrowing. PC checkpoint: 22 route/persona/history/Art cases passed, TypeScript and focused lint passed. Both actor pages now use common Art; final bridge/version-transition regressions follow before Task 10 is complete.

Task 10 complete: 283 actor/client/related/controller cases verified across the final run and focused corrections; the wrapper passed its earlier checkpoint. Backend Art reads and greeting-copy source preservation: 35 passed. Final TypeScript, changed-file ESLint, repository ESLint (727 at baseline), and Ruff (1136 at baseline) passed. New-version creation guards before writing; confirmed localization and Chub downloads invalidate Art. The Chub SSE fixture's doubled newline escapes were corrected before its passing focused run.

### Task 11: Five entity kinds inside the existing detail layout

**Files:** Modify `components/EntityEditor.tsx`, `EntityEditor.test.tsx`, `routes/WorldView.tsx`, `WorldView.test.tsx`, and `index.css`.

**Interfaces:** EntityEditor consumes supported `tab=art` modifiers for its selected record and creates a target without actor version. View mode renders Details/Art inside `editor-body`; existing record index, metadata sidebar, and prose Edit remain. One controller supplies Art, compact primary/gallery preview, count, focus, and phone entry.

- [x] Add `test.each` for locations/lore/items/groups/creatures in world/campaign scope: Details initially has rendered body/sidebar and no textarea or image management shelf; Art opens the same shared panel; Edit/New retain their established forms. Assert query-selected Art, unknown/error count, primary-only/gallery-only/empty phone entry, and focus-aware header/rail refresh.

  ```ts
  expect(screen.getByRole("tab", {name: "Details"})).toHaveAttribute("aria-selected", "true");
  expect(screen.getByRole("tab", {name: /^Art/})).toBeVisible();
  expect(detailPane.querySelector("textarea")).toBeNull();
  ```

- [x] Run `npm --prefix frontend test -- src/components/EntityEditor.test.tsx src/routes/WorldView.test.tsx`; expect old inline management/no tabs to fail.
- [x] Add selected-record tabs and compact contained preview; move image CRUD entirely to the shared panel. Preserve metadata/references navigation, content previews, existing Edit/Save/Cancel, record rail scrolling, and the app/context-column distinction. Replace the image-read `.catch(() => [])` behavior with the controller's visible failure state. Supported query modifiers survive WorldView canonicalization, without giving greeting routes Art modifiers.
- [x] Add `test_art_navigation_cancel_keeps_entity_and_draft` and `test_failed_art_does_not_suppress_entity_body`. Run focused tests plus typecheck; expect pass. Commit the entity integration and any independently passing mobile/layout refinement separately. Do not replace the rails with a record grid.

Entity checkpoint: 246 editor/router/controller cases passed, plus 9 focused phone/loading/New cases. Entity previews live in main, so the same Art entry remains reachable on a phone. Existing WorldView canonicalization already retains the query; regression tests now also assert the actual Art pane. ESLint baseline decreased from 727 to 717 after deleting the old shelf. Final owner-changing sidebar guard checks follow.

Task 11 complete: 197 final entity/library/panel cases passed, following 246 editor/router/controller cases and nine focused phone/loading/New cases. Final TypeScript, focused library/panel lint, full ESLint (717 at baseline), and whitespace checks passed. Reclassification and world-library removal guard Art drafts before their writes; changing an editor's kind re-resolves the selected record. The optional existing library-removal guard preserves its normal confirmation flow.

### Task 12: Combined gallery, library tiles, and existing drawers

**Files:** Modify `components/ImagesView.tsx`, `WorldArtPanel.tsx`, `RecordDrawer.tsx`, and existing ImagesView/RecordDrawer tests; create `components/WorldArtPanel.test.tsx` and `components/art/galleryState.ts`/its test. Retain campaign library mutation UI in `PostImagePicker.tsx` with only descriptor/entry wiring needed by existing navigation.

**Interfaces:** `galleryState` stores only route UI state keyed by scope/history entry: filters/search, selected placement key, scroll position. Clear it on store-root remount. Gallery descriptors navigate through `sectionHref` into Art for all seven kinds and actor versions; world/campaign library destinations use their existing entry points. Drawers keep the existing `DrawerTarget` union and read art lazily for resolved scene actor version or location overlay.

- [x] Write tests for case-insensitive search over record/version/description composing with kind/unfinished/appearance filters; no-results versus truly empty copy; and viewer sequence excluding greeting entries. Assert greeting tiles retain their existing full detail/subject behavior and tagging navigation, including explicit-empty subjects.
- [x] Write `test_gallery_record_navigation_and_back_restores_state` with a real memory data router: filter, scroll/select, open viewer, Open record, return Back, and verify filters/selection/scroll restoration. A library link must never guess a campaign world-images section.

  ```ts
  expect(searchInput).toHaveValue("map");
  expect(restoredFilters.kind).toBe("locations");
  expect(restoredSelection).toBe(selectedPlacementKey);
  expect(restoredScroll).toBe(scrollBeforeRecordNavigation);
  ```

- [x] Run `npm --prefix frontend test -- src/components/ImagesView.test.tsx src/components/art/galleryState.test.ts src/components/RecordDrawer.test.tsx`; expect new integration tests to fail.
- [x] Replace record/library gallery Detail with the common viewer; keep the separate greeting Detail. Add local search and restored UI state without a semantic index. WorldArtPanel uses shared tiles/viewer while retaining current library CRUD/description/usage contracts. Drawer metadata may fail independently of readable prose; opening full art preserves drawer/transcript and scene-specific actor version. Initial preview uses metadata/thumbs, not every original.
- [x] Add `test_location_drawer_reads_inherited_art`, `test_drawer_viewer_escape_returns_focus`, and `test_world_library_has_no_primary_or_actor_version`. Run focused tests and existing PostImagePicker/WorldArtPanel tests; expect pass. Commit gallery/history, library display, and drawer integrations separately when each passes.

Checkpoint verification: 255 gallery/library/viewer/client cases and 166 App/WorldView/path/router cases passed. Final typecheck and changed-file lint passed; ESLint fell from 717 to 710. Gallery and library navigation/display land together because the typed campaign-manager deep link immediately depends on the existing picker management display mode. Drawer integration was committed separately after the verification below.

Drawer verification: five new requirements failed before integration; 27 drawer/viewer cases then passed. Final Task 12 suite passed 102 cases and typecheck passed. Focused drawer lint retains its four pre-existing backdrop click findings with no new warnings. Metadata errors are independent of prose; actor versions resolve from the scene, locations use the overlay, and viewer Escape restores focus without closing the drawer.

### Task 13: Explicit Markdown viewing and stable collection occurrences

**Files:** Modify `markdown/MarkdownImage.tsx`/its test, `components/GreetingMarkdown.tsx`/its test, `components/play/StreamingMarkdown.tsx`/its test, and current non-greeting consumers in EntityEditor, PCPage, RecordDrawer, IncomingReview, and ModuleContentEditor. Create `markdown/MarkdownArtProvider.tsx`/its test.

**Interfaces:** `MarkdownArtProvider({enabled,contextKey,children})` owns viewer state outside individual streamed image nodes; renderer/context identity is stable. Greetings explicitly set `enabled=false` while retaining `ImageExtrasContext`. Non-greeting consumers opt in. Collection display descriptors keep canonical manifest indices as keys and start at the concrete member currently selected by that occurrence; viewer movement is temporary.

- [x] Write `test_inline_collection_viewer_starts_at_current_member`, `test_two_occurrences_remain_independent`, and `test_stream_block_completion_preserves_open_viewer`. Assert the selected inline src and canonical indices survive viewer stepping/closing and structural stream updates; do not reroll on enlargement. Available members preserve original index names even after another member fails.
- [x] Add greeting regression tests asserting no common viewer, unchanged extras/subjects/localization/tagging, and unchanged collection controls. Add linked-image tests requiring a separate enlarge button rather than nested interactive elements; plain external HTTP(S) images have no mutation/import/model call or proxy and no promised Download.

  ```ts
  expect(inlineImage).toHaveAttribute("src", memberSelectedBeforeOpen);
  expect(otherOccurrence).toHaveAttribute("src", itsIndependentSelection);
  expect(screen.queryByRole("dialog", {name: "Image viewer"})).not.toBeInTheDocument();
  ```

- [x] Run `npm --prefix frontend test -- src/markdown/MarkdownImage.test.tsx src/markdown/MarkdownArtProvider.test.tsx src/components/GreetingMarkdown.test.tsx src/components/play/StreamingMarkdown.test.tsx`; expect missing explicit integration to fail.
- [x] Integrate the stable opt-in provider and image renderer with Task 7's viewer. Preserve current manifest validation, per-occurrence selection, previous/next/reroll, canonical member URLs, and image extras. A manifest fallback opens only its rendered fallback image. Unsupported sources retain ordinary rendering. Keep provider outside stream block splitting so finalizing a paragraph cannot unmount its viewer; scope/source disappearance closes it honestly.
- [x] Add `test_failed_member_is_not_silently_replaced_in_viewer` and `test_non_greeting_remote_image_has_no_record_controls`. Run focused tests plus IncomingReview/ModuleContentEditor tests; expect pass. Commit explicit opt-in/greeting preservation and collection/stream integration separately when each passes.

Core checkpoint: explicit provider, greeting opt-out, independent collection selection, canonical member keys, separate linked controls, and streaming viewer ownership are verified. Initial 48 cases and typecheck passed; collection metadata rerender regression failed then passed. Final core/consumer run passed 90 cases. New source lint has no findings; GreetingMarkdown retains its four existing findings.

Consumer verification: incoming/module opt-in regressions failed before wiring and passed afterwards. Existing EntityEditor, PCPage, and RecordDrawer suites passed with the integration. Across final runs, 295 distinct cases passed; the last core/consumer run passed 90, and typecheck passed. Greetings remain explicitly disabled both in their renderer and incoming greeting previews. No model/import/proxy request is added.

### Task 14: Full coverage, isolated behavior verification, and final gates

**Files:** Update feature tests as needed; create `docs/superpowers/validation/2026-10-11-unified-image-surfaces.md` only when there are actual results to record. Change lint baseline files only for verified improvements. Update this plan's checkboxes as work lands, committing documentation checkpoints regularly.

**Interfaces:** Consume all preceding deliverables; no new product interface. Completion requires all sixteen spec acceptance cases, both mandatory implementation reviews, and honest evidence for gate/browser/Android status.

- [ ] Confirm the backend/frontend seven-kind by two-scope matrices exercise every common management operation, not merely tab presence. Verify cross-kind/library reuse, explicit greeting source/target rejection, inherited/shadowed/read-only behavior, both description-conflict scenarios through retry, no extra blobs on reuse, and legacy/export compatibility. Run focused feature and regression tests once with current changes; failures are fixed in their owning task.
- [ ] Use the repository verify skill to launch an isolated store/mock model with synthetic tall portrait, wide map, narrow item, tiny art, and animated GIF/WebP; use existing placeholder records only. Follow the browser skill to check desktop and phone layouts, touch/pinch/drag, no overflow, focus/scroll restoration, layered Escape, all seven records, dirty Back cancellation, filtered-gallery return, partial uploads/primary reuse, shared-caption reconciliation, and streamed collections. A missing browser tool means browser verification remains outstanding; it never becomes a claim of success from source tests.
- [ ] Exercise the same web behavior in the packaged Android WebView under `docs/android-architecture.md`. Run `make check-apk` after Android bootstrap when build prerequisites are present; report APK build and interactive WebView checks separately. No native-library addition is planned.
- [ ] Run `make check PY=C:/Users/charl/github/grimoire/backend/.venv/Scripts/python.exe`. Expected: all constituent gates pass, including pydantic1 and required coverage floors. If fixing a ratcheted lint finding lowers a baseline, run `make baseline` and commit the smaller baseline with that fix; never weaken guards/floors. Do not regenerate frozen-campaign `home/`.
- [ ] Run the mandatory `/codex:review` on the implementation diff, then `/codex:adversarial-review` on diff **and originating spec**, explicitly asking for missing requirements/drift. Resolve findings, add targeted regression tests where needed, and rerun affected checks/reviews. Record unavailable evidence plainly; an unavailable mandatory review is not passed.
- [ ] Validate the final diff with `git diff --check`, inspect staged files/privacy, and commit the passing checkpoint. Leave the branch reviewable in this worktree; integration/squash is a later action. Report observed results, material limitations, and outstanding verification without claiming completion where a required check remains.

## Coverage and ordering

Tasks 1-5 produce the common backend contract. Task 6 consumes that contract; Task 7 can be built independently of data fetching. Tasks 8-9 establish navigation/state before page adoption; Tasks 10-13 then consume those stable interfaces. Task 14 accepts the complete seven-source feature together.

| Spec requirement | Implementing tasks |
|---|---|
| Seven peer record kinds, greeting exclusions, scope/version addressing (§§3, 9) | 1, 4, 6, 10, 11, 13 |
| Details/Art, identity preview, zero/unknown/error count, phone entry (§4) | 8-11 |
| Typed bookmarks, replace/push history, dirty context changes, Back restoration (§§4.3, 7.1) | 8, 9, 12 |
| Contained tiles, sequence, natural fit/400%, touch/pan, modal ownership, lazy originals (§§5-6) | 7, 9, 12, 13 |
| Effective provenance, read-only originals/other versions, separate Appears in (§6.2) | 1, 9, 10 |
| Sequential batch upload, server allocation, occupancy/name locks, GC safety (§§7.2, 10.2) | 2, 9 |
| Scoped cross-kind/library reuse, legacy captions, primary partial recovery (§§7.3, 10) | 4, 6, 9 |
| Journalled swaps, entity focus/summary tokens, preview-only crop (§7.4) | 3, 7, 9-11 |
| Description states, shared/legacy wording, write-target visibility, CAS/reconciliation, partial cleanup recovery, draft endpoints (§§7.5, 10.1) | 3, 6, 9 |
| Placement removal, inherited tombstones, narrative-slot warning, no automatic primary/GC (§7.6) | 3, 9 |
| Combined search/filter/greeting detail, library display, existing scene drawers (§§8.1-8.2) | 12 |
| Markdown opt-in, greeting opt-out, linked/remote/fallback art, stable collection indices/stream (§8.3) | 13 |
| Additive APIs, old caller compatibility, download cache separation, Android/pydantic (§§9-10) | 1-6, 14 |
| Honest read/write/partial/refresh failures, shared invalidation, late-result refusal, privacy (§11) | 6, 9, 12-14 |
| Sixteen acceptance cases, full matrix, isolated browser/Android and gate evidence (§12) | 14, with tests in each owning task |

## Review and execution handoff

Before implementation, the complete plan must pass the repository's mandatory
adversarial plan gate and the user selects an execution method. Neither is
implied by the approved spec. Preserve the user's liberal checkpoint-commit
instruction during either execution method.

Native execution is recommended: these tasks share the same scope, placement,
description-state, and mutation interfaces, so one implementer can keep them
consistent without repeated context handoffs. Subagent-driven execution remains
an available user choice under its dedicated skill; it adds independent task
reviews and greater context cost. No subagents have been dispatched for writing
this plan.

Task 14 checkpoint: all source-review findings and follow-up edge cases are fixed. Final affected suite passed 95 cases and typecheck; structural Markdown/greeting/modal suite passed 63; route read matrix passed 35. The baseline target succeeded with Ruff 1136, mypy 161, ESLint 707. A real isolated backend verified an initially empty store and passed 14 seven-kind/scope media reads plus cross-kind reuse, shared-caption refresh, and stale-save refusal. The ordinary plugin review and Browser runtime failed on missing Windows sandbox support; inline plugin review is pending, as are full gate, adversarial review, APK build, and interactive browser/WebView evidence. See the validation document for the sixteen-case mapping and actual limitations.


Task 14 native review checkpoint: the inline built-in Codex review completed and
found sixteen additional defects. The controller, publication, refresh ordering,
viewer centering/notices, picker, default-version, identity/removal disclosure,
and drawer corrections are covered by 358 passing frontend cases and 18 passing
destination-deletion cases. TypeScript checking passes. Two stale assertions
from the aggregate frontend suite now check the deliberately fresh-read
contracts. All required aggregate gates and final plugin reviews are still
pending; browser/WebView acceptance remains unavailable, not passed.

Task 14 second native checkpoint: ten further findings required corrections.
Focused frontend verification passed 449 cases; final TypeScript checking and
119 backend reuse/write cases passed. make baseline passed with ESLint 706,
Ruff 1136 and mypy 161; no increased finding counts were accepted. The previous aggregate
passed 4,824 frontend cases and coverage, but its backend phase and APK build
were stopped for the review fixes. Task 14 remains incomplete; aggregate,
packaging and final mandatory reviews are pending. Browser/WebView interaction
remains unavailable, not passed.

Task 14 third native checkpoint: all seven further P2 findings are corrected.
Frontend268 and TypeScript pass; backend137 reuse/write cases pass, including18
legacy-restoration publication races. Existing line-diff IDs exceeded Windows'
environment limit; shortening IDs preserves all inputs/assertions and34 cases
pass. APK packaging passed at the preceding checkpoint, separately from unavailable
interactive WebView evidence. The complete gate and native review are rerunning
on the corrected source; the final adversarial implementation/spec gate follows.

Task 14 fourth native checkpoint: all five findings are fixed. Final focused
frontend242, backend158, TypeScript, scoped Art ESLint and Ruff pass. The
unchanged EntityEditor findings match their recorded baseline. Source fixes
are committed separately; the fifth full native implementation review runs
before the next aggregate gate. The previous APK build passed, while browser
and interactive WebView acceptance remain outstanding. Final adversarial
review and complete backend/Pydantic aggregate results remain required.

Task 14 fifth native checkpoint: four P2 findings and two related follow-ups
are corrected with RED/GREEN evidence. Final frontend308/15files, TypeScript,
scoped Art lint, backend351 race/lock cases and Ruff pass. Route identity now
retires every viewer, accepted navigation exits Manage, actors have a visible
main-heading focus fallback, unavailable connected openers are skipped, and
campaign upload/legacy reuse preparation stays outside long campaign holds.
Corrections are committed separately. Sixth native review, complete aggregate,
final adversarial review and packaging rerun are pending; interactive browser
and WebView evidence remains unavailable.
Task 14 sixth native checkpoint: four P2 findings are corrected. Initial shared
caption alternatives are disclosed before Save, Markdown viewers close after
accepted same-context Back, dirty or pending library captions guard every viewer
and dismissal path, and zoom remains at least Fit after viewport changes.
All 595 affected frontend tests in 18 files and TypeScript pass. Scoped lint
reports only the picker's two existing baseline findings. Four corrective
checkpoints are committed. Seventh source review and APK rebuild are next;
complete aggregate and final adversarial review remain required. Browser and
interactive WebView evidence remains outstanding.

Task 14 seventh native checkpoint: three P2 findings are corrected. Superseded
Art snapshots cannot escape to caption reconciliation, explicit caption choices
retire pending reloads, and accepted same-record Back retires the reuse picker.
All five entity kinds now reuse MobileArtEntry with the specified View art action
for primary, gallery-only and empty records. This supersedes the earlier Task 11
phone-entry interpretation; entity rails remain. The visible entity heading
restores focus after width changes hide a desktop opener. Final verification
passes 388 frontend tests in nine files and TypeScript. Scoped lint exactly
matches baseline, with no Art findings. Three correction commits are complete;
eighth source review and APK rebuild precede the aggregate and final adversarial
gates. Interactive browser/WebView acceptance remains outstanding.

Task 14 eighth native checkpoint: five P2 findings are corrected in separate
controller and panel/picker commits. Explicit-empty save intent remains guarded,
stale crop reload can be retried inside its modal, reuse labels identify actor
versions, superseded post-save reads do not claim failure, and uploads report
active file/completed/total progress. Verification passes 96 frontend tests,
a six-case picker rerun, TypeScript and scoped ESLint.

The complete aggregate passed 4,887 frontend tests and coverage, lint ratchets,
templates and TypeScript, but backend failed with 15 failures, 18,129 passes,
33 skips and 94.16% coverage. Pydantic 1 was not reached. Two genuine Windows
compatibility bugs (raw CRLF archive parsing and overlong sidecar stat errors)
are fixed and committed; 63 owning tests and Ruff pass. Existing portability
fixtures are being corrected, and config PermissionErrors plus absorb timing
remain under investigation. The next aggregate uses the default four workers
with unchanged selection and coverage floor. Current-source APK and ninth native
review precede final adversarial review. Interactive acceptance remains open.

Task 14 compatibility checkpoint: 215 existing portability tests pass with
three skips, and five cancellation/retry/concurrency tests pass. The only
absorb fixture that requires its peers to survive now grants local preparation
headroom while keeping its slow phase ten times beyond the ceiling; a serial
RED established the former limit expired before audit started. Original outcome
assertions remain. Ruff ratchet passes at 1136. Current-product APK packaging
passes in 1m 56s (48 tasks, 10 executed, 38 up-to-date). Ninth native review and
the full default-four-worker gate follow on committed source.

Task 14 ninth native checkpoint: disappearing world originals now preserve
campaign effective art and surviving originals, and conflict labels correctly
identify placement-local legacy text. Both gaps have observed RED/GREEN cases.
The P1 promotion-lock premise is ruled out: the unchanged repair helper switches
to nonblocking acquisition while image stripes are held; four forced-collision
cases cover both scopes and both promotion paths. No lock implementation change
was made. Verification passes 476 backend cases, 92 affected frontend cases,
TypeScript, Ruff ratchet 1136 and scoped ESLint. Corrections are committed
separately. Updated APK passes in 2m 20s (48 tasks, 10 executed, 38 up-to-date).

The preceding current-frontend aggregate passed 4,895 cases and coverage, but
its backend phase was stopped at about 45% for these corrections with two
unidentified failure markers. A verbose first-failure diagnostic precedes the
next complete gate. Tenth native review receives the unchanged locking helper
bodies along with the full diff/spec. Full backend/Pydantic results and final
adversarial review remain open; interactive evidence remains unavailable.


Task 14 tenth native checkpoint: recovered primary reuse now retires its retry
without promoting the former primary, and accepting replacement art retires an
older caption reload. Gallery reads and their independently required tagging
companions retain separate errors; gallery-only success cannot clear companion
failure, full Retry recovers it, and pending companions are never labelled empty.
Nine regressions have observed RED/GREEN evidence. Verification passes 134
frontend cases, six backend wording guards, TypeScript and scoped ESLint.
Corrections are committed separately at f31c6faad and b64fcd9c5. The unchanged
backend diagnostic and Pydantic 1 suite continue; final aggregate, updated APK
and both final source-review verdicts remain required. Interactive acceptance
remains open.


Task 14 Windows read checkpoint: the full Pydantic 1 suite passes 18,154 cases
with 41 skips at the preceding backend checkpoint. The modern diagnostic stops
after 17,914 passes on a scene-record read denial during replacement. A real
isolated NTFS probe reproduces that CRT EACCES shape without winerror. The
bounded initial-open correction commits at 34ede3ff1; 108 owning cases pass
with four existing skips, including permanent/missing/POSIX/error-after-open
behavior and temporary-path/public-API guards. Ruff 1136 and mypy 161 remain
unchanged. The corrected real probe completes 76,491 reads without a read denial.
This does not change writer error classification or replace the final gate.

Task 14 eleventh native checkpoint: four RED cases establish fallback focus
scroll loss, campaign-owned override removal disclosure and initial legacy
write-target labels. Corrections pass 119 affected frontend tests, TypeScript
and scoped ESLint, and commit separately at e27825984 and 9c4e6e232. Scroll
simulation is unit evidence; interactive browser/WebView behavior remains open.
The previous APK at 89ad47b67 builds successfully; explicit native-exit capture
also verifies make exit 0. Updated packaging, final complete aggregate and both
final source-review verdicts remain required on the latest committed source.

Task 14 current-product APK checkpoint: make exits 0, BUILD SUCCESSFUL in
1m 35s, 48 tasks (10 executed, 38 up-to-date), at a5fbb5f39. All current
frontend/backend corrections are packaged in build/grimoire-debug.apk.
Twelfth native review and the complete named-test aggregate follow; interactive
browser/WebView acceptance remains unavailable, separately from packaging.


Task 14 current-frontend checkpoint: the complete gate passes 4,911 frontend
cases in 228 files and coverage (88.82% statements, 84.12% branches, 84.20%
functions, 91.45% lines). TypeScript, templates and all three lint ratchets
pass at 1136/161/706. Modern backend coverage remains active with the same
93% floor, followed by Pydantic 1. Current real HTTP v6 starts empty and passes
all 14 media reads plus cross-kind reuse, shared-caption refresh, stale-caption
refusal, campaign append and inherited legacy reuse. The verified owned server
was stopped. Interactive acceptance and both final source-review verdicts
remain open. Only documentation changes after the tested product checkpoint.

Twelfth source-review checkpoint: one P1 recovered-slot reuse overwrite and two
P2 viewer gaps reproduced. The owned complete-gate tree was stopped before
backend edits at about43percent, after frontend4911 and all baseline phases
passed. Eight backend and three viewer RED cases pin the corrections; owning
checks, a new complete gate, fresh source review and final adversarial verdict
remain required. Short-screen visual acceptance remains explicitly outstanding.

Twelfth corrections committed separately: viewer1b70cc5f9 and reuse4ca4db049.
Final122frontend and667backend tests pass (one existing backend skip), types
and scoped lint pass, Ruff1136/mypy161 unchanged. Corrected historical RED
covers all eight recovery cases; the initial four primary fixture errors are
recorded transparently in validation. Thirteenth native review and APK are
running; complete current-source aggregate and adversarial review remain open.

Current-product APK passes at4ca4db049: make exit0, BUILD SUCCESSFUL3m41,
48tasks10executed38up-to-date. Real isolated HTTPv7 passes all14 media reads,
cross-kind reuse, shared-caption refresh/stale409, campaign append and legacy
reuse. Its uniquely owned four-process tree was stopped and port8199 released.
The previous aggregate reached47percent before termination; its worker-crash
markers are interrupted results, not completed assertion diagnoses.

Thirteenth source-review checkpoint: two P1 recovery-order defects reproduce
with controlled stripe release. The owned gate was stopped before backend
edits; terminal interruption boundary29percent, after4914frontend tests and
all baseline phases passed. Required recovery barrier73a5a954d now passes
34 new cases plus667 surrounding cases (one existing skip), Ruff1136/mypy161.
Corrected historical replay gives20RED/14positive controls. Native14 review
is running. Updated packaging, real HTTP and complete aggregate remain required;
interactive Browser/WebView acceptance and final adversarial remain open.

Current73a5a954d packaging and isolated HTTP pass: APKmake exit0, Gradle
BUILD SUCCESSFUL1m9,48tasks8executed40up-to-date; HTTPv8 starts empty and
passes14 media reads, cross-kind reuse, shared-caption refresh/stale409,
campaign append and legacy reuse. Only its proven owned four-process tree
was stopped afterwards; port8199 released. Native14 verdict is pending before
another complete aggregate, avoiding a needless interrupted backend run.

Fourteenth review corrections committed: detached-root recovery54529e1dc,
exhausted collections2cc962e68 and pending-context ownership1c242f5c4. All216
owning backend and171 affected frontend tests pass; types and scoped lint
pass, Ruff1136/mypy161 unchanged. Standard APK15 passes (make0, Gradle2m32),
and real HTTPv9 starts empty and passes14 media reads plus reuse/caption/
campaign/legacy checks. The verified owned server was stopped. Android device
is now available; a separate verification package is building to keep its
store isolated. Native15, the full aggregate and adversarial review remain
required; no Browser/WebView acceptance pass is claimed.

Android UI acceptance is now deferred by the user: the separately identified
verification package built and installed successfully, but the user requested
no further device interaction. No capture/seeding/UI pass was performed. The
test package remains installed; all further device actions are stopped.

Fifteenth native findings fixed: transient viewing keeps upload/reuse ownership
separate from editor generation, and PC save refresh preserves the accepted
URL. Five corrected historical regressions fail before the fixes; all202
affected frontend tests pass, with TypeScript and scoped ESLint0. Full
repository checks are handed to CI on a draft PR at the user's request. The
owned local gate was intentionally stopped after Ruff/mypy/templates; no
coverage/compatibility pass is claimed. Final adversarial review remains
required. Desktop browser connection is unavailable and Android interaction
is deferred by the user.
