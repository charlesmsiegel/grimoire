# Export — Remaining Work (COMPLETED)

> Picked up from `2026-05-12-export-design.md` (companion). All non-deferred items now ship; sections 12, 13, 14 are still explicitly v2.

**Companion (shipped earlier):** `2026-05-12-export-design.md`
**Module:** `backend/src/grimoire/export/`
**Bundled plugins:** `backend/bundled_plugins/export-*/`

## 1. Wire `EpubAdapter` into the running service — DONE

Approach (a): `main.py` now constructs `EpubAdapter(sources, config=…, filter_defaults=…)` and registers it as a built-in alongside the plugin adapters (`adapters=[epub_adapter, *container.plugins.export_adapters()]`). EPUB is no longer plugin-gated and `POST /campaigns/{id}/export adapter_id="epub"` works end-to-end.

## 2. Preview + history + list-adapters HTTP routes — DONE

Added three new routes in `api/campaigns.py`:

- `GET /campaigns/{id}/exports/adapters` — returns id, name, extensions, mime_type, `capabilities` (model_dump) and `option_schema` per registered adapter.
- `POST /campaigns/{id}/exports/preview` — same payload shape as `/export`, returns `ExportPreview`.
- `GET /campaigns/{id}/exports?limit=N` — returns the persisted `ExportRecord` list (newest-last, optional `limit` trims).

Conformance test coverage: `tests/api/test_campaigns_routes.py::test_list_export_adapters_returns_capabilities` and siblings.

## 3. Persisted export history — DONE

New SQL migration `020_export_records.sql` creates the `export_records` table keyed by `id`, with columns for adapter_id, JSON-serialised selection/options/result, `world_versions_json` and `created_at` (plus `idx_export_records_campaign`). `ExportService` now accepts `state_store=…`; when set, `_record_history` also persists each record and `history()` lazily reloads from disk if the in-memory bucket is empty. Library-version capture pulls `WorldSource.get_composition_worlds` defensively so the spec-13 §Responsibilities requirement ("against what library versions") is satisfied.

In-memory behaviour is unchanged when `state_store` is None — existing unit tests pass without modification. New regression test: `tests/export/test_persisted_history.py`.

## 4. `ExportYAML` configuration block + `Settings` wiring — DONE

New `export/config.py` defines `ExportConfig`, `ExportAdaptersConfig` (`epub` / `markdown` / `json`), `ExportFiltersConfig` and `EpubAdapterConfig`. `Settings` exposes the block as `settings.export`. `main.py` threads:

- `settings.export.adapters.epub` into `EpubAdapter(...)` so `default_options()` honours `default_style` and `include_appendices_by_default`.
- `settings.export.filters` as `filter_defaults=` into the adapter and the service's `preview` path.
- `ExportServiceConfig.from_export_config(...)` carries `output_directory`, `default_adapter`, `history_limit` through.

`build_snapshot._build_filter_context` honours `filters.*_default` only when `selection.filters` doesn't override the same key, matching the spec.

## 5. Per-arc selection — DONE

Used the tag-convention approach (lower-impact than touching Scene Manager / storage): scenes tagged `arc:<id>` are matched by `ExportSelection.arcs=["<id>", …]`. Wired in `build_snapshot._scene_matches_arcs` and in `selection.filter_scenes(arcs=…)` for FS-based plugin adapters. Frontend selector is the natural follow-on but out of backend scope.

## 6. Cover-image auto-generation via ImageGen — DONE

New `CoverGenerator` Protocol in `export/sources.py`. `DataSources` carries an optional `cover_generator` field. `EpubAdapter._maybe_generate_cover` is invoked from `export(...)`: when `options.cover_image is None` and either `extra["generate_cover"]=True` or `EpubAdapterConfig.default_cover_generated=True`, it composes a prompt from title/subtitle, the first world's genre/description, and the first chapter's title/mood, then asks the generator for bytes. Failure paths add warnings and fall through to the plain title page. `main.py` ships a `_ImageGenCoverGenerator` that wraps `ImageGenService.generate_sync`. Off by default. New test: `test_epub_auto_cover_pulls_from_generator`.

## 7. EPUBCheck wired by default — DONE

`EpubAdapter` now reads `epubcheck_path` from either the explicit constructor argument **or** `EpubAdapterConfig.epubcheck_path`, and `default_options()` sets `extra["validate"] = config.validate_with_epubcheck`. With `settings.export.adapters.epub.validate_with_epubcheck=True` (default) and an `epubcheck_path` supplied, validation runs without user-side wiring. EPUBCheck failures remain non-blocking — folded into `result.warnings`.

## 8. Source-attribution labels in output — DONE

`ExportOptions.extra["show_source_attribution"]` (off by default) toggles attribution rendering in the EPUB world / locations / lore / factions / items appendices: a small italic `<source: wod-london v7>` chip is appended to each entry's `<dt>` using `LibraryEntity.world_id` joined against the captured `WorldMeta.version`. CSS additions in both presets. New test: `test_epub_attribution_renders_source_label`.

## 9. POV consolidation filter — DONE

Implemented at the snapshot level (chosen over per-adapter to keep adapters dumb). `FilterContext.pov_consolidation_mode` accepts `"off"` (default), `"by_kind"` (adjacent posts with the same `author_kind` collapse), and `"by_author"` (adjacent posts with the same `author_kind` *and* `author_display` collapse). Selection passes the mode through `filters.pov_consolidation`. Bodies are joined with a blank line; the first post's labelling is preserved. New test: `test_pov_consolidation_by_kind_merges_adjacent_posts`.

## 10. Mechanical events as footnotes (EPUB) — DONE

`_render_chapter` now extracts `[roll|mech|stat|sheet|result …]` chips from post bodies, replaces them inline with `<a epub:type="noteref">[kind]</a>` references, and emits a `<section epub:type="footnotes">` block of `<aside epub:type="footnote">…<a epub:type="backlink">↩</a></aside>` entries at chapter end. CSS for `.mech-noteref` and `section.footnotes` added to both style presets. Gated on the existing `extra["include_mechanics_footnotes"]` option. New test: `test_epub_emits_real_footnotes_for_mech_chips`.

## 11. Adapter `capabilities` field on the Protocol — DONE

`ExportAdapter` Protocol now declares `capabilities: ExportCapabilities`. Every shipped adapter (the in-tree `EpubAdapter` and all five bundled plugins) carries a `ClassVar[ExportCapabilities]` describing image / appendix / filter / style-preset support honestly. The conformance suite still tolerates `getattr(..., "capabilities", None) is None` so older third-party plugins don't break, but every adapter Grimoire ships now declares one. New test: `test_epub_adapter_declares_capabilities`.

## 12. Print-ready PDF adapter — DEFERRED to v2 (unchanged)

## 13. Multi-volume / auto-split exports — DEFERRED to v2 (unchanged)

## 14. Incremental "export new content since last export" — DEFERRED to v2 (depends on §3, which now ships)

## 15. Continuity-in-share-exports default policy — DONE

Added `ExportSelection.audience: str | None` (values `"personal" | "share" | None`). When `audience == "share"`, `build_snapshot` excludes the `continuity` appendix even if the caller explicitly requested it — so OOC fact/commitment ledgers don't leak into share exports. `"personal"` and `None` leave the current explicit-opt-in behaviour intact. New tests: `test_share_audience_excludes_continuity_appendix`, `test_personal_audience_keeps_continuity_appendix`.

---

## Files touched

**Source:**
- `backend/src/grimoire/config.py` — surface `export: ExportConfig` on `Settings`
- `backend/src/grimoire/export/config.py` (new) — `ExportConfig` + per-adapter / per-filter blocks
- `backend/src/grimoire/export/service.py` — `state_store` support, persisted history, capabilities on Protocol, filter defaults in preview
- `backend/src/grimoire/export/snapshot.py` — arc matching, audience policy, POV consolidation, filter defaults
- `backend/src/grimoire/export/selection.py` — arc filter for FS adapters
- `backend/src/grimoire/export/filters.py` — `pov_consolidation_mode` on `FilterContext`
- `backend/src/grimoire/export/sources.py` — `CoverGenerator` Protocol + `DataSources.cover_generator`
- `backend/src/grimoire/export/epub.py` — capabilities, config-driven defaults, real footnotes, source attribution, cover autogen
- `backend/src/grimoire/types/export.py` — `arcs` and `audience` on `ExportSelection`
- `backend/src/grimoire/main.py` — wire EpubAdapter, cover generator, state-store-backed history
- `backend/bundled_plugins/export-{markdown,single-markdown,json,html,transcript}/plugin.py` — declare `capabilities`
- `backend/src/grimoire/api/campaigns.py` — three new export HTTP routes
- `backend/src/grimoire/storage/migrations/020_export_records.sql` (new) — persisted history table

**Tests:**
- `backend/tests/export/test_remaining_features.py` (new)
- `backend/tests/export/test_persisted_history.py` (new)
- `backend/tests/api/test_campaigns_routes.py` — three new tests for the new HTTP routes
