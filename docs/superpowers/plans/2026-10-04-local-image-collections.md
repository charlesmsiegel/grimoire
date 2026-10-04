# Local Image Collections Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox syntax for tracking.

**Goal:** Preserve rotating image pools as permanent local collections with random initial display and browsing controls.

**Architecture:** Final manifests contain only ordered local content-addressed image names. A separate private harvest journal owns source addresses until publication and reference replacement finish. Existing image routes, atomic writes, process locks, and Markdown rendering own their existing responsibilities.

**Tech Stack:** Python 3.11+, FastAPI, Pillow, existing guarded HTTP downloader, React 18, react-markdown, pytest, vitest.

**Spec:** `docs/superpowers/specs/2026-10-04-local-image-collections-design.md`

## Global Constraints

- Final manifests are format 1 with nonempty, unique members; no source URLs or refresh behavior.
- Sampling defaults: 30 consecutive valid duplicates, 200 requests per invocation, three consecutive failures; resume obtains fresh evidence.
- UUID collection ids; full SHA-256 member names; first-discovery order; no perceptual deduplication.
- Existing atomic writers and filesystem resolvers; no new dependencies or private identifiers in repository artifacts.
- Network operations outside collection-storage locks. Per-job exclusive locks protect journals.
- Native execution and local review follow the user's instruction to proceed without another external-review permission round.

## Review Focus

- Greeting extras must not write world-library member names into greeting-owned subject metadata (Task 4).
- A streaming paragraph becoming a closed block must retain image selection (Task 4).
- A failed request interrupts the duplicate streak, and a request cap leaves a resumable journal (Task 2).
- Referenced-image deletion and manifest publication must serialize; campaign tombstone cleanup happens outside the collection lock (Task 1).
- Publishing a manifest before relinking finishes must retain source recovery information (Task 6).

## File Structure

- `store/image_collections.py`: source-free manifests, member validation and publication, membership guards.
- `store/image_collection_imports.py`: journal-backed sampling, configurable limits, final publication.
- `store/locks.py`: collection-store and harvest-job locks using existing process-lock machinery.
- `store/world_images.py`, `routes/world_images.py`: shared member protection, read API and fallback.
- `markdown/MarkdownImage.tsx`: shared image component and extras context.
- Existing greeting, streaming, and record Markdown renderers: integrate the shared component.
- `store/export.py`: deterministic collection representative packed with existing exports.
- Private runner under the private home's `.cache/`: inventory, backups, exact relinking, serving verification, failure CSV update, completion receipts.

### Task 1: Collection store and protected members

**Files:** Create `backend/src/grimoire/store/image_collections.py`, `backend/tests/test_image_collections.py`; modify `store/locks.py`, `store/world_images.py`, `store/__init__.py`.

**Interfaces:** Produce `put_member(wid: str, data: bytes) -> str`, `publish(wid: str, collection_id: str, members: list[str]) -> dict`, `read(wid: str, collection_id: str) -> dict`, `available(wid: str, collection_id: str) -> list[dict]`, `referenced(wid: str, name: str) -> bool`, `url_for(wid: str, collection_id: str) -> str`. Add `image_collection_lock(wid)` and `image_collection_job_lock(wid, job)`.

- [x] Write store tests for deduplication, validated immutable publication, traversal, missing members, concurrent publication, and protected writes/deletes. Two identical inputs produce one stored member; a protected delete raises `ImageInCollectionError`.
- [x] Run new tests with worktree `PYTHONPATH`; observe missing-module failure.
- [x] Implement the interfaces using assets, covers' existing structural image validation, atomic writes, and existing lock registry. Keep collection storage independent of `world_images` to prevent an import cycle. Release the collection lock before dependent campaign cleanup.
- [x] Run collection and world-image tests; run import/atomic guards.
- [x] Commit the store deliverable and record results in the execution ledger.

### Task 2: Resumable finite-pool harvesting

**Files:** Create `backend/src/grimoire/store/image_collection_imports.py`, `backend/tests/test_image_collection_imports.py`.

**Interfaces:** Consume Task 1; produce `sample(wid, source_url, *, duplicate_limit=30, max_requests=200, failure_limit=3, delay=0.25, fetch_image=None) -> dict`, `accept(wid, job_id) -> dict`, `job_path(wid, job_id) -> Path`. `sample` returns job id, collection id, members, invocation counters, and stop reason. `accept` freezes a candidate and preserves recovery state until the private runner completes replacement.

- [x] Write fake-response tests for new/new/duplicate streams, threshold saturation, request-limit incompleteness, invalid bytes, three failures, interrupted streaks, resume, and repeated acceptance. Assert `requests == valid + failed` and `valid == added + duplicates`.
- [x] Run tests and observe missing-module failure.
- [x] Implement an exclusive job invocation, persistent progress after every response, network outside the collection lock, explicit candidate acceptance, and no resampling after publication.
- [x] Run store/import tests and confirm a final manifest has neither a source nor a harvesting state.
- [x] Commit the harvester deliverable and update the ledger.

### Task 3: Read API and portable exports

**Files:** Modify `backend/src/grimoire/routes/world_images.py`, `backend/src/grimoire/store/export.py`; create `backend/tests/test_image_collection_routes.py`; extend `backend/tests/test_image_collections.py`.

**Interfaces:** Consume Task 1. Produce `GET /api/worlds/{wid}/image-collections/{id}` with format, id, ordered local member URLs, and `GET .../{id}/image` serving first available member. Export resolves against the exported campaign's world and packs a first available member deterministically.

- [x] Write tests for metadata, fallback bytes, all-missing 404, corrupt manifests, protected PUT/DELETE 409, deterministic export packing, and bundle/fork repointing under another world id.
- [x] Run tests; observe the new routes and export resolver fail.
- [x] Add routes to the existing world-images router before generic entity routing; reuse `_serve_image_file`. Extend export URL recognition and lookup without changing ordinary image resolution.
- [x] Run route-order, export, world bundle/fork, and collection route tests.
- [x] Commit the API/export deliverable and update the ledger.

### Task 4: Stable random display and controls

**Files:** Create `frontend/src/markdown/MarkdownImage.tsx`, `frontend/src/markdown/MarkdownImage.test.tsx`; modify `GreetingMarkdown.tsx`, `GreetingEditor.tsx`, `play/StreamingMarkdown.tsx`, `index.css`, and existing Markdown record previews.

**Interfaces:** Produce `MarkdownImage`, stable `markdownImageComponents`, and `ImageExtrasContext`. Recognize only same-origin collection paths; fetch the local manifest and validate every returned member URL. Consumers get the selected image's actual URL.

- [x] Write tests for random start, Previous/Next wrapping, reroll avoiding the current image, independent occurrences, source changes, stale fetches, missing members, one-member controls, fallback, ordinary images, and streaming stability. Assert rerendering the same occurrence does not choose again.
- [x] Install existing frontend dependencies and run the new tests; observe missing-component failure.
- [x] Implement collection state in a stable component, context-based extras, accessible controls, same-origin manifest validation, and local fallback. Scope greeting-specific extras to owned image URLs; use span wrappers inside Markdown paragraphs.
- [x] Run component, greeting, and streaming tests plus typecheck; build the frontend.
- [x] Commit the frontend deliverable and update the ledger.

### Task 5: Repository verification and whole-branch local review

**Files:** Existing check targets; update this plan and execution ledger with results.

**Interfaces:** Consume all implemented interfaces; produce a reviewed feature branch and built frontend.

- [x] Run `make check PY=C:/Users/charl/github/grimoire/backend/.venv/Scripts/python.exe` and inspect every result. Use ratchet baselines correctly; no new lint debt.
- [x] Review the whole diff against the originating spec, with particular attention to missing-image fallback, lock ordering, resumed journals, and selection stability. Use the executing-plans skill's fresh reviewer when available.
- [x] Pin material findings with failing tests, fix them, and rerun affected checks.
- [x] Commit verified changes and confirm existing main-checkout edits are untouched.

### Task 6: Private harvesting, migration, and live verification

**Files:** Private cache runner, journals, backups, report, and completion receipts only; private world records and failure CSV as authorized by the user.

**Interfaces:** Consume sample/accept, published collection APIs, and the built app. Produce verified local collections and replacement references, with report counters.

- [x] Inventory explicit source addresses and exact image references, correcting only known aliases. Back up all affected records and the failure CSV.
- [x] Sample every source to the duplicate threshold; request-limit or failure outcomes remain resumable. Accept only verified candidates. Keep source mapping until relinking and HTTP verification finish.
- [x] Activate the verified worktree build on the existing local service without switching or modifying the main checkout. Perform migration in an editing-quiescent window; compare each target immediately before atomic replacement. Report detected edits rather than overwrite them. External-editor races are outside existing atomic-write guarantees.
- [x] Verify every member's served bytes, all replacement references, and absence of runtime source URLs. Remove only resolved CSV entries, retire transient mappings, and preserve historical backups.
- [x] Return collection/image/request/duplicate/failure totals and any remaining sources; do not claim mathematical exhaustion from random sampling.

Verification results and review rulings: [verification report](2026-10-04-local-image-collections-verification.md). Repository-wide Windows limitations are recorded there; checks were run without raising baselines.
