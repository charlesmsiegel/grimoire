# 08-S5 The `searchdocs` hook, `vectors_for`, and the `history-index` task — Plan

**Spec:** [08](../specs/2026-10-09-roadmap-08-history-search-documents-design.md), sections 8 and 9, slice 08-S5. **Delivers:** 08-C2b in full, 08-C2c in full, 08-C3a in full. **Needs:** 08-S3, plus 05-S6 and 03-S4, which have landed on main. That puts 05-C3's `WarmHook` protocol in `store/cache_sync.py`.

## Where it differs from the spec's sketch, and why

- **The protocol on main is `owns(kind)`, not `kinds()`.** Vector kinds carry a space digest, so ownership is a test. The hook owns `searchdocs.scene` and every `vector:searchdocs.scene:<digest>`. A row in another space is still the hook's, and it answers it `skipped` with `stale_space`, as the `vectors` hook does. It is not reported `lazy`.
- **The hook class lives in `cache_sync.py` and delegates to `searchdocs/hook.py`.** `cache_sync` imports its hooks, and the module graph must stay acyclic (`test_import_guard.py`), so `searchdocs` cannot import `cache_sync` for `LocalResult`. This is the overview hook's arrangement. `hook.py` holds the work as plain module functions returning plain data (`rebuild`, `reembed`), and the class in `cache_sync` folds that data into `LocalResult` and `NetworkResult`. The functions are not named `local` or `network`: `test_operation_guard.py` refuses any `.network(...)` call that is not a hook's, handed `ctx`.
- **The space is `ctx.space`, not a fresh `embed_space.endpoint()`.** The operation guard accepts the batch's space only through a hook's own `network` method, and the batch resolves it once (`_context`). A role moved between the phases is refused as the `vectors` hook refuses it (`stale_space`, nothing sent).
- **The hot rows name the documents.** Every `searchdocs.scene` and vector row carries `{campaign, identity}` and sits on every input file the document read (08-S3). So the documents a hot path touches are exactly the identities on its rows. A world file's rows already name each campaign whose documents read it, so no walk of `world_refs()` is needed. A campaign whose world reference does not resolve never recorded the world's files (08-S3: its world layer is dropped and `campaign.md` is degraded). Its documents do not read them, so a world edit has nothing to rebuild there; its `campaign.md` is a recorded input instead. An identity no live scene carries is a deleted scene, or one whose `sid` was recycled under a new identity. It is `skipped`, which is the spec's identity check.

## Tasks

- [ ] `routing.EMBED_TASKS` gains `"history-index"`; `test_inference_embedding.py` names it.
- [ ] `embedded.vectors_for(cid, docs, *, space, client, limit) -> DocVectors`: `vectors.load` under `space["space"]`, `warm_window(misses, cid, limit)`, one `embed_sync("history-index", ..., campaign=cid, cached=, uncached=, queries=0)` with no scene, `vectors.save`, then `record_embedded`. Never raises: an `LLMError` or `OSError` is `error=<kind>`. `DocVectors` also carries `sent`, the texts the request carried, which the sync report counts.
- [ ] `live.build(cid, only, *, store)` returns the live set and the keys rendered this call. `scene_documents` is the `store=True` case, and a dry run stores nothing. `live.identities(cid)` maps each live identity to its sid.
- [ ] `searchdocs/hook.py`:
  - `owns`.
  - `REINDEX_LIMIT = embeddings.BATCH - 1`.
  - `rebuild(rows, *, space, space_digest, embedding, dry_run, halted)`. It rebuilds the hot documents per campaign (`built` or `hit`, `planned` in a dry run, `skipped` for an identity gone) and judges each vector row. A row in another space is `stale_space`, embedding off is `embedding_off`, text held is `hit` (its rows refreshed), embed off is `deferred`, and otherwise the row is planned per campaign.
  - `reembed(plan, *, space, halted)`. It calls `vectors_for` once per campaign. The result is `built` when held, `lazy` when outside the window, `failed:<kind>` on error. A failure other than `bad_response` stops the rest.
  - Its own `_CLIENT`.
- [ ] `cache_sync`: `_SearchDocsHook` (`name="searchdocs"`, `on_write="explicit"`) wraps the two functions; `HOOKS = (FILES, OVERVIEW, SEARCHDOCS, VECTORS)`.
- [ ] `MIN_EMBED_CALLS = 7`; CLAUDE.md's embedding paragraph names the new caller.
- [ ] Tests (`test_searchdocs_embed.py`):
  - `vectors_for`:
    - shares 09's key both ways;
    - sends at most `limit`, on a rotating window, and a refused document stops nothing else;
    - files one `history-index` ledger row, campaign set, scene empty;
    - on failure, returns what was saved and the kind.
  - The hook, through `cache_sync.sync_paths`:
    - three documents, two embedded; a `chronicle.json` sync rebuilds three and re-embeds only the embedded one whose text changed;
    - a world location edit rebuilds hot documents in each campaign of the world, and a campaign with its own copy embeds nothing;
    - a recycled `sid`'s old row re-embeds nothing;
    - a moved space and embedding off send nothing;
    - write-through defers the hook;
    - `rebuild` sends nothing (the fake asserts it), and the report holds no text;
    - a dry run stores and sends nothing.

## Verification

`pytest tests/test_searchdocs_*.py tests/test_cache_sync*.py tests/test_operation_guard.py tests/test_inference_embedding.py tests/test_import_guard.py`, the full suite, `make check-lint check-mypy`.
