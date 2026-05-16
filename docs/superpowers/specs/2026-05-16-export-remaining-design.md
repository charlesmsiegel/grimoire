# Export — Remaining Work

> Everything from the original `specs/13-export.md` (now superseded) that did **not** land in the shipped design (`2026-05-12-export-design.md`). Use this as the input to a writing-plans pass when picking up the work.

**Companion (already shipped):** `2026-05-12-export-design.md`
**Module:** `backend/src/grimoire/export/`
**Bundled plugins:** `backend/bundled_plugins/export-*/`

## 1. Wire `EpubAdapter` into the running service

Today `main.py:200-212` only registers adapters that came from `container.plugins.export_adapters()` and there is **no bundled `export-epub` plugin** — only the five non-EPUB formats ship as plugins. The in-tree `EpubAdapter` (`export/epub.py`) is constructed by tests and is part of the public `grimoire.export` surface, but in a running app calling `POST /campaigns/{id}/export` with `adapter_id="epub"` raises `UnknownAdapterError`.

Two reasonable shapes:

- (a) Construct `EpubAdapter(sources, epubcheck_path=settings.export.epubcheck_path)` in `main.py` and pass it as a built-in via `adapters=[epub, *container.plugins.export_adapters()]`.
- (b) Move EPUB to a bundled plugin under `backend/bundled_plugins/export-epub/` so all adapters travel the same registration path.

Spec 13 names EPUB the v1 priority format, so (a) is the lower-risk fix; (b) is cleaner long-term but means relocating `epub.py` (and its 800+ test lines) into the plugin tree.

## 2. Preview + history + list-adapters HTTP routes

`ExportService.preview(...)`, `ExportService.history(...)`, and `ExportService.list_adapters(...)` are implemented and tested but nothing in `api/campaigns.py` exposes them. Spec 13 §Interface lists them as part of the Export surface. Needed:

- `GET /campaigns/{id}/exports/adapters` — return id, name, extensions, mime_type, capabilities, and option_schema per registered adapter
- `POST /campaigns/{id}/exports/preview` — same payload shape as `/export`, returns `ExportPreview`
- `GET /campaigns/{id}/exports` — return `ExportRecord` list (paged or limited)

## 3. Persisted export history

`ExportService._history` is a per-process `dict[CampaignId, list[_HistoryEntry]]` capped at `config.history_limit`. The module docstring (`export/service.py:1-7`) explicitly notes that "persisted history (`ExportRecord` rows in the State Store) is a follow-on the orchestrator wires up; here we keep the interface stable so it slots in later."

Needed: a State Store table (likely `export_records`) keyed by `(campaign_id, export_id)`, with `_record_history` and `history` reading/writing through it. Records should capture not just selection/options/result but also the library version(s) the snapshot was resolved against, per spec 13 §Responsibilities ("Track export history (what was exported, when, with what worlds, against what library versions)").

## 4. `ExportYAML` configuration block + `Settings` wiring

Spec 13 §Configuration defines a full `export:` block:

```yaml
export:
  default_adapter: epub
  output_directory: ./exports
  adapters:
    epub: { default_style, include_appendices_by_default, validate_with_epubcheck,
            default_cover_generated }
    markdown: { default_filename_format, include_assets }
    json: { pretty_print, include_embeddings }
  filters:
    strip_ooc_default: true
    strip_mechanics_default: false
    anonymize_default: false
```

Today `ExportServiceConfig` only carries `output_directory`, `default_adapter`, and `history_limit`; the per-adapter and per-filter defaults are not threaded through. Add a `grimoire.config` dataclass for the block, surface it through `Settings`, and have:

- `main.py` pass adapter-specific config into the EPUB / plugin adapters
- `build_snapshot._build_filter_context` honour the `filters.*_default` knobs when `selection.filters` doesn't override
- `EpubAdapter.default_options` honour `default_style` and `include_appendices_by_default`

## 5. Per-arc selection

Spec 13 §Per-scene and per-arc exports calls for "user-marked 'arc'" as a first-class selection unit. `Scene` (`scenes/types.py`) has `key_beats: list[str]` and `tags: list[str]` but no `arc_id` field, and `ExportSelection` has no arc field. Needs:

- A Scene Manager affordance to mark scenes as part of an arc (or reuse `tags` with a convention like `arc:Saint-Werewolf`)
- `ExportSelection.arc_id` (or `arc: list[str]`) plumbed into `build_snapshot` so scenes get filtered by membership
- A frontend selector that surfaces the arcs back to the user

If reusing tags is acceptable, this collapses to a snapshot-side filter and a docs note; if a dedicated `arc_id` is needed, it touches Scene Manager and the storage layer.

## 6. Cover-image auto-generation via ImageGen

Spec 13 §Open questions includes cover generation. `ExportOptions.cover_image: bytes | None` is already supported by `EpubAdapter._add_cover_image`. Needs: an opt-in option (e.g. `options.extra["generate_cover"] = True`) that, when no `cover_image` is supplied, asks ImageGen to render a cover prompt derived from the campaign metadata and the snapshot's first chapter, then injects the resulting bytes before `_build_book`. Per the original spec: off by default.

## 7. EPUBCheck wired by default

Today EPUBCheck only runs if the caller both sets `options.extra["validate"]=True` **and** passed `epubcheck_path=` to `EpubAdapter(...)`. Production wiring never supplies `epubcheck_path`. With §4 (config block) in place, surface `export.adapters.epub.validate_with_epubcheck` and `export.adapters.epub.epubcheck_path` through `main.py` so on-by-default validation works without user code.

## 8. Source-attribution labels in output

Spec 13 §Purpose: "Source attribution is optional in output (most users want the polished read; some want 'from `wod-london` v7' labels on appendix entries)." The snapshot tracks `LibraryEntity.world_id` and the resolved `WorldMeta` list, but no adapter renders the attribution. Needed:

- An `ExportOptions.extra["show_source_attribution"]: bool` flag
- EPUB / markdown / HTML appendix renderers append `<source: wod-london v7>` (or equivalent) when the flag is set

## 9. POV consolidation filter

Spec 13 §Filter and transformation hooks lists POV consolidation as a sixth filter. `filters.py:10-12` explicitly defers it as "a structural concern more than a text one — left to the adapter". Either:

- Implement it at the snapshot level by reordering / merging adjacent `FormattedPost`s with the same author_kind, or
- Implement it inside each adapter's chapter renderer

Pick one. Either way it needs a `FilterContext.pov_consolidation_mode` knob.

## 10. Mechanical events as footnotes (EPUB)

`_render_chapter` already understands `options.extra["include_mechanics_footnotes"]` but the current implementation just appends the raw post body as a `.mech`-styled paragraph after the formatted prose — it does not extract per-event annotations into actual EPUB footnotes (`<a epub:type="noteref">…<aside epub:type="footnote">`). Spec 13 §EPUB adapter calls for "mechanical events as inline annotations or footnotes (user choice)". Needs: parse mechanical chips out of the post body, replace them with footnote references, and emit the footnote bodies as `<aside>` elements at the chapter end.

## 11. Adapter `capabilities` field on the Protocol

Spec 13 §Format adapter interface includes `capabilities: ExportCapabilities` on the adapter. `ExportCapabilities` exists in `types/export.py` but `ExportAdapter` (`service.py:32-49`) does not require it; the conformance suite reads it via `getattr(..., "capabilities", None)`. Decide whether to require it on the Protocol (and add it to every shipped adapter), or formalise the "optional" status by removing it from the spec. Either is fine; the asymmetry is the bug.

## 12. Print-ready PDF adapter (v2; deferred)

Spec 13 §Open questions: "PDF adapter could wrap EPUB-to-PDF via Calibre or Pandoc. Likely v2." Recorded so it doesn't get re-litigated.

## 13. Multi-volume / auto-split exports (v2; deferred)

Spec 13 §Open questions: "For 200-session campaigns, single EPUB is unwieldy. Auto-split by arc, by date range, or by user breakpoint." Deferred to v2.

## 14. Incremental "export new content since last export" (v2; deferred)

Spec 13 §Open questions: adapter-level support for chapter-pack exports of new content. Depends on §3 (persisted history) shipping first. Deferred to v2.

## 15. Continuity-in-share-exports default policy (open)

Spec 13 §Open questions: "Default: include as appendix in personal exports, exclude in 'share' exports." Today the `continuity` appendix is included only when the caller explicitly lists it in `selection.include_appendices`; there is no notion of "personal vs share" mode. Either define the modes (and add a `selection.audience: Literal["personal","share"]`) or close the question by adopting the current "explicit-opt-in" behaviour and removing the distinction from the spec.

---

## Suggested plan ordering

If picking this up, a reasonable order:

1. §1 — wire `EpubAdapter` into production so the v1 priority format actually works end-to-end
2. §2 + §4 — surface `preview` / `history` / `adapters` over HTTP and add the config block they need
3. §3 — persist export history so the new `GET /exports` route returns something interesting across restarts
4. §7 + §11 — small adapter-surface fixes (EPUBCheck wired, `capabilities` story consistent)
5. §6 + §8 + §10 — EPUB polish (auto-cover, source attribution, real footnotes)
6. §5 + §9 — selection / filter additions that touch Scene Manager or every adapter
7. §15 — close (or implement) the personal-vs-share continuity question
