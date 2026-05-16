# State Store — Remaining Work

> Everything from the original `specs/03-state-store.md` (now superseded) that did **not** land in the shipped design (`2026-05-12-state-store-design.md`). Use this as the input to a writing-plans pass when picking up the work.

**Companion (already shipped):** `2026-05-12-state-store-design.md`
**Modules:** `backend/src/grimoire/state_store/`, `backend/src/grimoire/storage/`, `backend/src/grimoire/watcher/`

## 1. Drain the embedding queue

`FileWatcher.embedding_queue` (`watcher/watcher.py:69`) accumulates `EmbeddingJob`s as files are indexed but nothing consumes them. Spec 03 §File watcher: "File created → parse, insert index row, queue embedding, emit event" assumes a worker calls into the active embedding plugin and writes vectors via `StateStore.add_embedding`.

Design needed:

- A background worker task (started in `main.py` lifespan) that drains the queue, batches into `state_store.config.library.embedding_batch_size` (default 50, currently unconfigured), calls the active embedding plugin for vectors, and persists via `StateStore.add_embedding`
- Progress reporting on the bus (the orchestrator already subscribes to `library_indexed`; a periodic `embedding_progress` event with `pending` / `done` lets the Frontend show a progress bar)
- Backoff + retry per failed batch
- Restart-resilience: on startup, before kicking off the worker, scan for `library_index` + `campaign_content_index` rows with no matching `embeddings` row and re-enqueue them so an interrupted run resumes

## 2. `state_store:` YAML configuration block

Spec 03 §Configuration defines a dedicated config namespace:

```yaml
state_store:
  library_root: ./data/library
  campaigns_root: ./data/campaigns
  database_path: ./data/campaigns.sqlite
  enable_wal: true
  vector_extension: sqlite-vec
  library:
    watch: true
    scan_on_startup: true
    embed_on_index: true
    embedding_batch_size: 50
    embedding_provider: sentence-transformers
  snapshots:
    enabled: true
    deduplicate_by_hash: false
  auto_backup: { enabled, interval_hours, retention_count, includes }
  retention:
    embeddings_for_retired_facts: 90d
    delta_log: forever
```

Today only `data_root`, `database_path`, `db_pool_size`, `enable_wal` are exposed (env-var-driven on `grimoire.config.Settings`). Need a layered config — file + env overrides — with this shape, plus wiring so the watcher / embedding worker / backup task / retention sweep actually read it.

## 3. Auto-backup

Spec 03 §Configuration includes `auto_backup` with `enabled`, `interval_hours` (default 24), `retention_count` (default 14), `includes: [library, campaigns, sqlite]`. The spec body also says: "Backups are `zip data/`." There is no backup task today.

Needs: a scheduler task that zips the configured subset of `data/` on the configured interval, prunes to `retention_count`, writes to a configurable backup directory, and emits a `backup_complete` event. Probably hooks into the existing scheduled-events table (`010_scheduled_events.sql`).

## 4. Retention policy enforcement

Spec 03 §Configuration:

```yaml
retention:
  embeddings_for_retired_facts: 90d
  delta_log: forever
```

Today retired facts keep their embeddings indefinitely (`facts.retired = 1` just hides them from search); the delta log never garbage-collects (which matches `delta_log: forever`, so no work here for v1). Need a periodic sweep that deletes `embeddings` rows whose `ref` points at facts retired more than `embeddings_for_retired_facts` ago.

## 5. `body_compressed` auto-summaries

`library_index.body_compressed` is in the schema (`001_indexes.sql:12`) but no code path writes it. Spec 03 §SQLite schema notes it is "optional auto-summary for background-tier inclusion" — i.e. a shorter version of the body used when the Context Builder includes the entity at background tier and the full body won't fit.

Needs: a summarizer (probably the existing LLM gateway with a dedicated task name like `library_summarize`) and a worker that fills `body_compressed` for new / changed library rows, similar to the embedding worker in §1. Should probably share the queue with embedding work since both kick off the same trigger.

## 6. `promote_to_library` on the StateStore protocol (judgment call: probably skip)

Spec 03's write API placed `promote_to_library(campaign_id, kind, campaign_entity_id, target_world_id, source) -> str` on the State Store. The shipped code instead lives on `library/service.py:441`, `characters/service.py:696`, `world/service.py:687` and calls into `store.get_emergent` + `store.write_library_file`. Functionally equivalent — the only reason to move it back onto `StateStore` would be if multiple callers wanted a single canonical entry point.

Picking this up means: pick one home (probably leave it where it is and update the spec to match), or build a thin `StateStore.promote_to_library` that delegates to the domain services. Listed here so it doesn't get re-litigated; recommend leaving the current shape.

## 7. Domain-specific write helpers on StateStore (judgment call: probably skip)

Spec 03 §Write APIs lists `add_fact`, `add_commitment`, `upsert_character_state`, `upsert_location_state`, `upsert_faction_state`, `advance_time` as methods on `StateStore`. Continuity (`continuity/service.py`) and Time Engine (`time_engine/`) own these surfaces today and route writes through the generic `StateStore.apply_delta`. Functional coverage is equivalent.

Recommend leaving the current shape: domain modules own their semantics and the State Store stays a generic delta sink. Listed here so the gap is acknowledged and not re-implemented as duplicate APIs.

## 8. `library_root` multi-root support (v2; deferred)

Spec 03 §Open questions: "Cross-library multi-root setups. v1 has one library root; v2 may support multi-root via a `library_root` qualifier." Out of scope for v1.

## 9. Snapshot deduplication by content hash (v2; deferred)

Spec 03 §Snapshots for pinned campaigns: "Storage cost: pinned snapshots can duplicate a lot of content. Deduplicate-by-content-hash is a v2 optimization." Configured in spec as `snapshots.deduplicate_by_hash: false`. Defer.

## 10. Per-campaign SQLite databases (rejected)

Spec 03 §Open questions: "Single SQLite file vs. per-campaign databases. v1 uses one shared file. Per-campaign would simplify sharing but complicate cross-campaign queries. Single file wins for now." Treat as **rejected** for v1.

## 11. Collaborative merge for concurrent library edits (v2; deferred)

Spec 03 §Open questions: "Concurrent edits to a library file. Last-write-wins with warning in v1; collaborative merge in v2." Last-write-wins is shipped (the watcher's `conflict=True` payload). Defer collaborative merge.

## 12. Scene file branching strategy (open; coordinate with Scene Manager)

Spec 03 §Open questions: "Scene file branching. Lazy copy vs. annotation-in-frontmatter — implementation detail to be decided during build." The Scene Manager owns scene file writes today; the orchestrator's `fork` flow calls `scene_manager.fork_scenes_for_branch(...)` to copy scenes (per the orchestrator design doc). That's lazy-copy in practice. Confirm during the next Scene Manager / fork pass that this is the chosen approach and the State Store doesn't need to do anything additional.

---

## Suggested plan ordering

If picking this up, a reasonable order:

1. §1 — embedding queue worker. Pre-req for everything else that depends on vectors being current, and the easiest standalone slice
2. §2 — `state_store:` config block. Pulling the existing env settings into a layered config unblocks §3, §4, §5 cleanly
3. §3 + §4 — auto-backup and retention sweep together; both are scheduled background tasks and can share a small scheduler abstraction (likely on top of the existing `scheduled_events` table)
4. §5 — `body_compressed` summarizer; same worker shape as §1 so can ride on the same queue infrastructure
5. §6 / §7 — only if a fresh round of brainstorming concludes the API should move back to `StateStore`; otherwise close as "shipped at a different layer"
6. §12 — coordinate with Scene Manager work the next time that module changes; no standalone State Store action needed today
