# 05-S6 The `vectors` hook and the three producers: implementation plan

**Goal:** 05-C3 in full except the `overview` entry (05-S5, waiting on 04-S9):
the `vectors` hook, its network phase, the per-unit replacement rule and the
lazy-limit cap, the space checks before sending, the producers' `on_write`
policies, `materialized` rows recorded by recall, the art catalog and library
search, and the operation-guard extension; and 05-C2's batching (chunks of at
most `embeddings.BATCH`, grouped by campaign, saved as they land).

**Spec:** sections 6.4 to 6.7, 11 (05-C3) and 13 ("The policy").

## What landed upstream (re-read before planning)

- 03's `vectors.load(sources=..., projection=..., instances=...)` and
  `vectors.save(source=..., projection=..., instance=...)` already record a
  `vector:<projection>:<space digest>` row with `built_from` = the source
  bytes' hash. No producer passed them.
- 01h-C5 landed: `inference.embed.attribute(claims)`, `EmbedGroup`,
  `embed_groups_sync(...)` (one row per group, `not_sent` after a
  connection-wide failure). Documents pass no `queries`.

## Design

- **Projections, owned by their producers.** Each producer exports
  `PROJECTION` and `projection_texts(path, data, instance)`, the producer's
  own text function over a file's bytes, so a warm-up and a read cannot
  disagree about what was embedded:
  - recall (`lore-entry`): `semantic.record_text(eid, data)`, the candidate
    `world_state` builds from an entity record (name or id, comma keys, body
    stripped; "" with no body). Its row's instance is
    `{"campaign": cid, "entity": [kind, id]}`, and `projection_texts` answers
    None when `overlay.entity_source(cid, kind, id)` (new: the file
    `read_entity` reads, campaign copy first) is no longer that path.
  - art (`art-description`): the image object's sidecar description,
    stripped and clipped as a candidate's is. Only a candidate with an image
    object records a row; a legacy description does not (lazy).
  - search (`search-passage`): `semsearch.document_texts`, the passage texts
    of a single-file document (`search.markdown_document`, the bytes form of
    `_markdown_doc`). `search._record_docs` tags such documents with their
    `source`; composite documents (a card with its sidecars, a PC, a fact)
    record no row. One row per embedded passage, `{"unit": n}`.
- **Recording.** Each producer passes `source=` (the file's bytes, read at
  save time, and only when they still yield the text that was embedded),
  `projection=` and `instance=` to `vectors.save`. A turn embeds recall's
  candidates while it holds its campaign lock, where 03 takes no SQL; so
  `vectors.save` parks such history (`_DEFERRED`, bounded) and
  `vectors.flush_deferred()` records it later -- at every request's and run's
  write-set handoff (`cache_warm.hand_off_async`, off the loop, outside every
  lock) and at the start of a sync batch -- dropping an entry whose root
  moved or whose source no longer holds the bytes the vector was made from.
- **The hook** (`cache_sync._VectorsHook`, `VECTOR_PROJECTIONS`). Local phase,
  per hot row: deferred in write mode for an `explicit` projection (search);
  `embedding_off` or `stale_space` (`skipped`) when the role names no space
  or the row's digest is not the batch's; the replacement text of the one
  unit the row names (a multi-unit row only at its own position, so a
  never-embedded tail is never planned), capped per path at the producer's
  lazy limit; `hit` when the cache holds it (its row then moves to the current
  bytes), `planned` in a dry run (`would_send`), `deferred` with
  `embed=False`, otherwise planned with its campaign claim.
  Network phase: re-reads `embed_space.endpoint()` and stops with
  `stale_space` if the digest moved; groups claims per task with
  `embed.attribute` (one campaign's text charged to it, shared or library-wide
  text unattributed); sends chunks of `embeddings.BATCH` through
  `embed_groups_sync` under the producer's own task (a literal per producer,
  `_embed_chunk`), re-checking the cache just before each, saving each chunk
  as it lands; a non-`bad_response` failure stops the rest (`not_sent`, the
  failure kind in the report). `remap` keeps a unit position and renames
  recall's entity id on a stated rename.
- **Report.** Network outcomes are final (they replace `planned`);
  `EMBEDDING_FIELDS` counts `would_send`, `sent`, `requests`, `held`,
  `stale_space`, `embedding_off`, `deferred`, `not_sent` and `failure`.
- **The operation guard** accepts `ctx.space` only inside a `network` method
  of a class whose instance `cache_sync.HOOKS` names, where `ctx` is that
  method's own unrebound parameter, and only while every `WarmContext` in the
  package is built in `cache_sync._context()` with a `space=` that traces to
  `embed_space.endpoint` and nothing `replace`s a context's space. Planted
  fails: a hook building its own context, `_context` with a literal space,
  `ctx.space` outside a hook, in `local`, with `ctx` rebound, and through
  `dataclasses.replace`. `WarmContext.embed` became `embedding` so it never
  reads as the client's `.embed`.

## Reconciliations with the spec

- Rows are written where a vector is saved, not touched on every
  `vectors.load` hit: touching would read every candidate's file on every
  turn. So a campaign whose recall only ever hit a vector another campaign
  embedded has no row of its own, and the warm-up of that text is charged to
  the campaign that embedded it. The rows age by 03's retention only when a
  text is never re-saved.
- Art's legacy descriptions and search's composite documents record no rows,
  and stay lazy.

## Tests (`tests/test_cache_sync_vectors.py`)

Recall's row names its projection, space digest, instance and `built_from`;
dropping the cache changes no recall answer; search records one row per
embedded passage; a legacy art description records nothing and an object
does; an edited entry is re-embedded once under `semantic-recall`, charged to
its campaign, and recall then sends only its query; `stale_space`;
`embedding_off`; a role moved between the phases sends nothing; dry run and
`--no-embed`; one changed passage embeds one passage; a partly embedded
transcript (units 1, 4, 9) never embeds its tail; a renamed transcript keeps
those selectors and an unchanged rename embeds nothing; two producers' rows
on one path stay apart, and write-through embeds recall's and defers
search's; a connection-wide failure stops the rest; a late rate limit keeps
the earlier chunks, one ledger row per chunk; shared text is unattributed and
one campaign's is charged to it; every producer projection is claimed in a
fresh process; the CLI in a fresh process leaves vectors recall then hits; a
real chat turn parks recall's history under its lock and its handoff records
it; parked history for an edited source is dropped. The operation guard's
planted cases are in `tests/test_operation_guard.py`.

## Gates

Substitute reviews, recorded in the PR; `@codex review` on the PR.
