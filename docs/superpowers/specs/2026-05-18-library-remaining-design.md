# Library — Remaining Work

> Everything from the original `specs/18-library.md` (now superseded) that did **not** land in the shipped design (`2026-05-12-library-design.md`). Use this as the input to a writing-plans pass when picking up the work.

**Companion (already shipped):** `2026-05-12-library-design.md`
**Module:** `backend/src/grimoire/library/` (plus `watcher/`, `world/`)

## 1. Auto-summary into `body_compressed`

Spec 18 §Indexing into SQLite includes `body_compressed TEXT` ("optional auto-summary for background-tier inclusion"). The column exists in `library_index` (migration `001_indexes.sql:12`) but `upsert_library_index` always inserts `NULL` and no producer writes it. Spec 02 §Background tier expects this field to feed compressed-summary injection.

Needs: a summarizer (probably an LLM call) wired to the watcher's `EmbeddingQueue` style of out-of-band processing, plus a backfill pass invoked from `scan_now` for prose-bearing kinds (`character`, `item`, `location`, `lore`, `faction`, `greeting`, `style_guide`). Skip files whose body length is already below a threshold (no summary needed). Re-summarize when `content_hash` changes.

## 2. Configuration surface

Spec 18 §Configuration lists a full `library:` block. None of it exists in `backend/src/grimoire/config.py` today. Concrete gaps:

- `library.root` — implicit at `<data_root>/library`; add an explicit knob.
- `library.watch: bool` — the watcher always starts; add a way to disable for headless / batch jobs.
- `library.scan_on_startup: bool` — `start(initial_scan=True)` defaults true, but there's no config-driven override.
- `library.indexing.embed_on_index: bool` — embeddings are always enqueued for embeddable kinds; no toggle.
- `library.indexing.embedding_provider` — wiring through to plugins exists, but the library config doesn't pin one.
- `library.version_pinning.default: pinned | track_latest` — `WorldRef.track_latest` defaults to `False` in code (pinned), but the wizard / API doesn't read a default from config.
- `library.version_pinning.snapshot_on_bind: bool` — always true today; spec allows toggling.
- `library.files.{character,location,item}_filename_pattern: "{id}.md"` — hard-coded across `state_store/paths.py` and the watcher classifier. Patternizing them is mostly cosmetic; unless a real use case appears, mark as **(v2; deferred)**.
- `library.files.encoding: utf-8` — hard-coded; same v2 stance.
- `library.promotion.confirm_required: bool` — the API never gates promotion behind a confirm flag.

Decision needed: pull the spec block into `Settings` as a `LibraryConfig` model, wire the toggles into `FileWatcher.__init__`, `EmbeddingQueue` enqueue gating, and `set_composition`'s default for new refs.

## 3. Snapshot-aware fork (branch fork → snapshot copy)

`fork` (in the orchestrator / state store) creates a new branch but the spec note in §Pinning says "Branch forks point at the same snapshot rows" — that's implemented in the read fallback (`_resolve_world_base` walks `branch_id` and falls through), but no explicit copy is made. Confirm this behavior matches spec, and if branches diverge after a campaign upgrade on `main`, decide whether the forked branch should keep its own snapshot row or continue sharing.

This is a low-priority correctness check rather than missing code.

## 4. Promotion: cleanup of the campaign-local emergent

Spec 18 §Promotion step 5: "Replace campaign-local record with a reference to the library row (or convert to an override if the campaign has continued mutations)." Today `LibraryService.promote_to_library` writes the library file and returns — the emergent file at `data/campaigns/<id>/emergent/<kind>/<asset>.md` is left in place. There's also no step 6 (embedding migration / relink).

Needs:

- After the library write, decide: was the emergent identical to what we just wrote? If yes, delete it (`store.delete_campaign_content_row` + `Path.unlink`). If no (the campaign mutated it post-promotion), convert it to an override file under `overrides/worlds/<target>/<kind>/<asset>.yaml` containing only the diff.
- Re-key embeddings from `campaigns/<id>/emergent/<kind>/<asset>` → `worlds/<target>/<kind>/<asset>` so retrieval doesn't double-count.
- Surface a `promoted_to_library` (or similar) event so subscribers can refresh.

## 5. Character promotion path

`WorldService.promote_to_library` explicitly rejects `kind == "character"` (`world/service.py:697`) with "character promotion goes through the Characters module (task #12)". Whatever lives at `backend/src/grimoire/characters/` either implements this or doesn't; confirm and either delete this gap (if shipped) or wire it through here.

## 6. Demotion (reverse promotion)

Spec 18 §Promotion: "Demote (reverse promotion) is supported: remove from library (delete file). Campaigns that referenced it get a dangling-ref warning and an option to copy down to campaign-local."

There is no demote operation. `LibraryService.delete_entity` will remove the file but won't:

- Notify dependent campaigns
- Offer to copy the deleted entity into each campaign's `emergent/` folder

Needs: a `demote(world_id, kind, entity_id)` method on `LibraryService` (or `WorldService`) that calls `dependents(...)` first, emits an `entity_demoted` event with the dependent list, optionally accepts a `copy_down_to: list[campaign_id]` argument, and only then calls `delete_entity`.

## 7. Save-back-to-library (override → library file)

Spec 18 §Overrides: "A 'Save back to library' action propagates an override into the underlying library file (writes the file, increments version, clears the override)."

Today overrides are written by `store.write_override` and consulted by `resolve`, but there's no method to fold an override back into the underlying library file. Needs a `save_override_to_library(campaign_id, library_id)` that:

1. Resolves the entity through the cascade to produce the merged final state
2. Calls `store.write_library_file` with that state (bumps `version`)
3. Deletes the override file + index row
4. Surfaces a diff preview before committing (UI concern; the service should return the before/after).

## 8. Upgrade-with-diff-preview UX hook

`upgrade_world_ref` returns an `UpgradeReport` with a `diff` mapping but no per-entity before/after content — just version numbers. Spec 18 §Version pinning: "Upgrade is a user action with a diff preview." A complete diff preview needs the *frontmatter + body* of each changed entity at both the old snapshot version and the new live version, so the frontend can render an inline diff before committing.

Today there's no "dry run" path. Add `preview_upgrade_world_ref(campaign_id, world_id) -> UpgradePreview` that returns the same data but doesn't mutate snapshots, and gate `upgrade_world_ref` behind it from the UI.

## 9. Directory rename / move detection

Spec 18 §File watcher: "Directory renamed → emit warning; require manual reconciliation." The watcher today receives per-file watchdog events for the renamed directory's contents (the `_WatchdogBridge` forwards both `src_path` and `dest_path`) but treats the moved files as plain deletes-then-creates with no warning. A rename of `worlds/wod-london/` → `worlds/london/` would silently re-key every row.

Needs a directory-event handler that recognizes the `on_moved` watchdog event for directories, suppresses the cascade of per-file delete/create events for the moved subtree, and emits a single `library_rename_detected` event the user must acknowledge.

## 10. Variants UI surfaces "also exists in" links

Spec 18 §Character variants: 'UI shows "Drizzt (faerun) — also exists in: mythic-europe."' `variants_of` is implemented on the service and exposed at `GET /library/variants/{kind}/{asset_id}`, but the frontend doesn't render the variant breadcrumb on entity detail pages. Wire it through.

## 11. World forks: "fork world" UI action

`WorldService.fork_world` is implemented and exposed at `POST /library/worlds/{world_id}/fork`. Confirm the frontend exposes a "Fork world" action and that `fork_world` rewrites every per-entity id appropriately. Spec note: "Easy via directory copy + id rewrite" — the current impl does a directory copy and re-seeds every entity through `write_library_file`, which preserves ids from the frontmatter. If two world variants are supposed to have *different* asset ids (so they're not auto-linked via `variants_of`), there's no id-rewriting step yet.

Either:

- Document that fork preserves ids (so the forked world's entities are automatically variants of the source), and accept that as the intended behavior, or
- Add an `id_suffix` / `rename_map` parameter to `fork_world` that rewrites ids on copy.

## 12. Renaming with variant-link preservation (v2; deferred)

Spec 18 §Open questions: "If a user renames `drizzt` → `drizzt-do-urden` in one world, the id-based variant link breaks. A `rename` operation that updates references is a v2 idea." Treat as **(v2; deferred)**.

## 13. Multi-library roots (v2; deferred)

Spec 18 §Open questions: "v1 has one library root. Multi-library (e.g., per-project libraries) is a future option; schema supports it via a `library_root` qualifier." The schema does not yet carry that qualifier. Treat as **(v2; deferred)**.

## 14. Library sharing tooling (v2; deferred)

Spec 18 §Open questions: "Zip a world folder, share it, unzip into another user's library." Supported by structure, no tooling planned for v1. **(v2; deferred)**.

## 15. Cross-variant location families (v2; deferred)

Spec 18 §Open questions: a v2 sync feature for "the same place exists across world variants with different mechanical layers." **(v2; deferred)**.

## 16. Parameterized greetings (v2; deferred)

Spec 18 §Open questions: greetings templated with PC name / patron at runtime. **(v2; deferred)**.

## 17. Snapshot deduplication (v2; deferred)

Spec 18 §Open questions: content-addressed snapshot store keyed by hash. The shipped design notes the cost; the optimization is **(v2; deferred)**.

---

## Suggested plan ordering

If picking this up, a reasonable order:

1. §2 — land the `library:` config block first; later items reference its toggles.
2. §4 + §5 — finish the promotion story (cleanup + characters), since promotion is the most visible half-built workflow.
3. §6 + §7 — demote + save-back-to-library; they share the dependents/diff/override-merge plumbing and complete the campaign-side write loop.
4. §8 — upgrade diff preview; only after §7 because both need the merged-entity reader.
5. §1 — `body_compressed` auto-summary; mostly self-contained, needs an LLM-call worker similar to the embedding queue.
6. §9 — directory rename detection; the watchdog event plumbing is independent of the rest.
7. §10 + §11 — UI surfaces (variants link, fork-world action); coordinate with the frontend plan.
8. §3 — branch fork snapshot semantics correctness check; can run anytime, low priority unless a bug appears.
