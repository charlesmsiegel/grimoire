# 08-S2 The scene SearchDocument, in process — Plan

**Spec:** [08](../specs/2026-10-09-roadmap-08-history-search-documents-design.md), sections 3–5 and 13, slice 08-S2. **Delivers:** 08-C1 except the `groups` slice. **Needs:** 08-S1 (landed in this branch), 03-S2 (landed). Pure and read-only; nothing calls it yet.

## Tasks

- [x] **`slices.py`.** `CampaignInputs.load(cid)` parses `chronicle.json`, `appearances.json`, `facts.json`, `relationship_history.json` and `continuity.json` once per stat signature in the package's own `statcache` pool, sized like the scene-head pool; `effective.records` for threads and commitments is memoized on `plot.json`, `commitments.json` and `continuity.json` together. A file that will not parse, or has the wrong shape, contributes nothing and is named in `degraded`, logged once per kind. Actor names resolve as `scene_cast` resolves them (`overlay.actor_root` at the locked version, through a new public `appearances.cast.display_name`) and location names through the overlay, each memoized on the file it read, falling back to the id. `scene_slices(inputs, head)` cuts the head, rolling, chronicle, cast, locations, threads, commitments, status, facts, relationships and links slices with the absorbed gate, timed links, absorb-minus-undo relationship rows, and whitespace collapse, lone-surrogate replacement and byte clipping to each item's cap. It also records the store files each slice read, for S3.
- [x] **`rolling.py`.** `intact_rolling(cid, head)` returns the stored summary only when `rolling_intact.view` says `prior` is it. Runs only for an unabsorbed scene with a summary. A facts-digest mismatch answers without reading the body.
- [x] **`scene.py`.** `render(slices)` lays out the twelve labelled lines with spec 4.3's caps (at most 5803 bytes by construction, `DOC_BYTES = 6000` backstop), builds the metadata of spec 4.4 (lists capped at 64 with `truncated`), and `key_of(slices)`.
- [x] **Facade.** `searchdocs` is registered on the `store` facade and its API snapshot, so importing the package never makes `dir(store)` depend on test order.
- [x] **Tests** (`test_searchdocs_scene.py`): spec section 13's 08-C1 list except Groups (S6), plus a re-pad.

## Deviations from the spec

- **The document key** is 03-C1's composite: the kind, the version, `BUILD` (`compiled.build_fingerprint`), the slice digests, and `degraded`. `degraded` is in it because a replaced lone surrogate reads like the character that replaced it. A first draft left `BUILD` to the cache key alone. The Codex review pointed out that 09 keys term statistics by this key, so a rendering change has to move it.
- **The intactness memo** is keyed on the scene file's stat signature, `regex.layers.signature` over the connections that produced the transcript's messages, and two values computed fresh on every call: the facts digest and the seated player's name. The spec instead listed the stat signatures of `appearances.json` and the current location's file. Those two values are exactly what the test reads from them, and they also cover a PC card rename that no stat of `appearances.json` would see.
- **`degraded` is per scene**: a campaign ledger that would not read, plus what this scene's own slicing replaced (a lone surrogate, an unreadable card).

## Verification

`pytest tests/test_searchdocs_scene.py`, the store-API, import, paths, atomic, lock-domain, pydantic and compiled guards, `make check-lint check-mypy`.
