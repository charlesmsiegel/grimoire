# 08-S3 The live set, the compiled cache, and recording embedded vectors — Plan

**Spec:** [08](../specs/2026-10-09-roadmap-08-history-search-documents-design.md), sections 6, 8.1, 8.3, 12 and 13, slice 08-S3. **Delivers:** 08-C2a in full; the no-network half of 08-C2b. **Needs:** 08-S2, 03-S1..S5 (landed). Nothing embeds; no route calls it until 09.

## Tasks

- [x] **Registry.** `compiled.SEARCHDOCS_SCENE = Kind('searchdocs.scene', 1, (), ('slices', 'degraded'), False)`. `scene.SEARCH_DOCUMENT_VERSION` is that kind's version.
- [x] **`live.py`.** `scene_documents(cid, *, exclude, only) -> LiveSet`. The live set comes from `list_scenes`; when that raises `OSError` (one unreadable head), a tolerant walk takes over, with `closed_by` derived by `scenes.read.resolve_groups`, now public. Each scene is sliced, then answered from two tiers: the in-process memo keyed by the document key (`DOC_MEMO_ENTRIES = 8192`), then one `lookup_many` of the rest. Misses are rendered and stored with a `materialized` row on every path their slices read, instance `{campaign, identity}`, in batches that fit `compiled.MAX_STAGED_ROWS`. Never raises for store damage; `CampaignNotFound` for a campaign that is not there.
- [x] **`embedded.py`.** `vector_kind(space) = "vector:searchdocs.scene:" + compiled.space_digest(space["space"])`. `record_embedded(cid, docs, *, space)` writes that kind's row on every input path of each document whose text has a vector under `space["space"]`, in staging-sized batches. Never raises.
- [x] **Frozen campaign.** The sweep gains `searchdocs.scene_documents[...]`: text and metadata with the sid, minus `identity`, `slices` and the key. It reads with the cache off so the read-only sweep still writes nothing. `snapshot.json` was regenerated, reviewed and checked to regenerate byte-identically.
- [x] **Tests** (`test_searchdocs_live.py`): spec section 13's 08-C2a list and the `record_embedded` test.

## Deviations from the spec

- **The memo is the first tier, not only the cache-off fallback.** Measured on `scripts/synth_library.py --worlds 1 --campaigns 1 --scenes 400`, a synthetic library:
  - Warm through the cache took about 85 ms.
  - Warm through the memo took about 40 ms. The batch lookup cost as much as all the slicing.
  - A cold call built and stored the documents in roughly half a second.

  So the cache is asked only for what the memo lacks: after a restart, or past the bound. "One batch lookup per call" still holds, and answers are identical either way. The cost: a long-lived process does not refresh `last_used` on rows it keeps answering from memory, so 03's LRU can evict them first. The next restart then rebuilds those documents.
- **No persisted input projections** (spec section 5, open question 5). The cold restart above, about 150 ms for 400 scenes with the cache warm, does not justify them yet.

## Verification

`pytest tests/test_searchdocs_live.py tests/test_frozen_campaign.py tests/test_compiled_guard.py tests/test_compiled.py`, `make check-lint check-mypy`.
