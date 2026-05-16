# Export — Design (Shipped)

> Captures the Export module as actually built. The matching "remaining" spec at `2026-05-16-export-remaining-design.md` covers everything from the original `specs/13-export.md` that did **not** land in this work.

**Commit:** `57f8636` — "Implement Export module + EPUB adapter (task 25)" (followed by `7d2c05a` cover-asset fix, `7706dc3` bundled markdown/json/html/transcript/single-markdown adapters)
**Module:** `backend/src/grimoire/export/`
**Bundled plugins:** `backend/bundled_plugins/export-{markdown,single-markdown,json,html,transcript}/`
**Tests:** `backend/tests/export/`, `backend/tests/bundled_plugins/test_export_adapters.py`
**Conformance:** `backend/src/grimoire/testing/conformance/export.py`

## Purpose

The Export module produces shareable, archivable, or printable artifacts from a campaign: EPUB books, markdown bundles, JSON dumps, HTML pages, and plain-text transcripts. Each format is an adapter conforming to a small Protocol; the in-tree `EpubAdapter` is the priority v1 format and is wired by tests, the five remaining v1 adapters ship as bundled plugins that the `PluginsService` registry exposes through `container.plugins.export_adapters()`.

Export is read-only over the live state. Two read paths exist by design:

- **`ExportService` + `build_snapshot`** — uses injected `DataSources` (Scene Manager, Characters, World, Continuity, ImageGen). The EPUB adapter and any in-process consumer go through this path.
- **`load_fs_snapshot`** — walks `data/campaigns/<id>/` directly to build an `FsCampaignSnapshot`. The bundled plugin adapters use this path because they're loaded standalone and have no access to the live services.

## Module surface

### Service (`export/service.py`)

```python
class ExportService:
    def __init__(self, *, sources: DataSources,
                 adapters: Iterable[ExportAdapter] | None = None,
                 config: ExportServiceConfig | None = None): ...

    def register(adapter) / unregister(adapter_id)
    def list_adapters() / get_adapter(adapter_id)
    async def export(campaign_id, adapter_id, selection, options,
                     *, output_path: Path | None = None) -> ExportResult
    async def preview(campaign_id, adapter_id, selection, options) -> ExportPreview
    async def history(campaign_id) -> list[ExportRecord]
```

`ExportServiceConfig` carries `output_directory` (default `./exports`), `default_adapter="epub"`, and `history_limit=100`. A per-campaign `asyncio.Lock` serialises concurrent exports on the same campaign; different campaigns export in parallel. History is **in-memory only** today — the docstring on `service.py:1-7` notes that persisting `ExportRecord` rows into the State Store is a follow-on the orchestrator will wire up.

### Adapter Protocol (`export/service.py:32-49`)

```python
@runtime_checkable
class ExportAdapter(Protocol):
    id: str
    name: str
    extensions: list[str]
    mime_type: str

    async def export(
        self, campaign_id: CampaignId,
        selection: ExportSelection,
        options: ExportOptions,
        output_path: Path,
    ) -> ExportResult: ...

    def default_options() -> ExportOptions: ...
    def option_schema() -> JsonSchema: ...
```

This is a *narrower* surface than `specs/13` proposed: there is no `capabilities` attribute on the Protocol (the conformance suite reads it via `getattr(..., "capabilities", None)` and tolerates absence — `testing/conformance/export.py:84-87`). `output_path` is passed in by the service so the adapter never decides where on disk to write.

### Public types (`types/export.py`)

`ExportCapabilities`, `ExportSelection`, `ExportOptions`, `ExportResult`, `ExportPreview`, `ExportRecord`. Differences from the spec dataclasses:

- `ExportSelection` adds `filters: Json` (a free-form dict the snapshot builder interprets).
- `ExportOptions` keeps `extra: Json` as the per-adapter escape hatch (style preset, include_appendices, validate flag, etc.).
- `ExportResult` adds `payload: bytes | None` so an adapter can return an in-memory artifact when the caller does not want disk output.

### Data sources (`export/sources.py`)

Five narrow `Protocol`s: `SceneSource`, `CharacterSource`, `WorldSource`, `ContinuitySource`, `ImageSource`, `PCSource`. The `DataSources` bundle wires concrete services in production (`main.py:200-212`) and accepts any subset in tests; missing sources are filled in with `_Null*` stubs that return empty lists. `DataSources.data_root` is an optional `Path` so adapters can resolve `ImageMetadata.file_path` entries that were stored as repo-relative paths.

### Snapshot builder (`export/snapshot.py`)

`CampaignSnapshot` is a single read-only dataclass holding everything an adapter could need: `scenes: list[ScenePart]` (each carrying the `Scene`, its formatted posts, and inline images), `characters`, `worlds`, `locations`, `lore`, `factions`, `items`, `greetings`, `facts`, `commitments`, `images`, `pc_names`, `warnings`, `data_root`. `ScenePart` precomputes `word_count`. `FormattedPost` carries `author_kind`, `author_label`, `author_display`, and the post body **after** filter application.

`build_snapshot` is the pipeline:

1. `pc_names = await sources.pcs.pc_names(...)` (used for anonymisation and display labels)
2. Build a `FilterContext` from `selection.filters` and `options.extra` (`_build_filter_context`)
3. Walk every scene for the branch; drop scenes that (a) are outside an explicit `scene_ids` allowlist, (b) are drafts when `include_drafts=False` (judged by `post_count==0`), (c) fall outside `date_range`, or (d) carry a tag in `filter_ctx.skip_tags`
4. For each surviving scene, fetch its posts, apply `apply_filters` to each body, drop empty bodies, and record the formatted post + cumulative word count
5. If `include_images`, ask `sources.images.list_images(campaign_id, scene_id=...)` per scene (tolerates `NotImplementedError`)
6. Resolve appendices from `selection.include_appendices`: `cast` → characters (anonymised if a mapping was configured); `world`/`locations`/`lore`/`factions`/`items` → walked per world via `WorldSource.list_in_world`; `continuity` → facts + commitments; `gallery` → campaign-wide images
7. Return the assembled snapshot

### Filesystem snapshot (`export/data.py`)

`load_fs_snapshot(data_root, campaign_id, branch_id)` reads `campaigns/<id>/campaign.yaml`, walks `scenes/*.yaml`+`.md`, and reads emergent + override entity cards from `emergent/<kind>/*.md` and `overrides/worlds/<world>/<kind>/*.yaml`. `SceneRecord` carries the `Scene` model plus the raw post tuples from `scenes.storage.read_posts`. Missing directories are silently tolerated. `selection.py` provides `filter_scenes(...)` (scene-id allowlist + drafts gate) and `filter_context_from_dict(...)` so the plugin adapters can translate the `ExportSelection.filters` mapping into a `FilterContext` without depending on the snapshot builder.

### Filter pipeline (`export/filters.py`)

Pure-text transforms composed in canonical order: `strip_ooc → strip_narrator_scaffolding → strip_mechanics → anonymize`, then whitespace normalisation. Each regex is compiled at import time. `FilterContext` holds the flags + `anonymize: dict[str, str]` + `skip_tags`. `anonymize_label(label, ctx)` is a separate helper for non-prose speaker labels.

Spec 13 listed six filters; this ship implements four (OOC, mechanics, narrator scaffolding, anonymise). POV consolidation is deferred (a structural concern, not a regex one) and per-tag scene filtering is handled at the snapshot level via `skip_tags`.

### EPUB adapter (`export/epub.py`)

Pure-Python EPUB 3 packager — no `ebooklib` dependency. The pipeline:

1. `_merge_appendix_choices` unions `options.extra["include_appendices"]` (default `("cast", "world", "calendar", "gallery")`) into `selection.include_appendices`
2. `build_snapshot(...)` resolves every scene + appendix
3. `_build_book` assembles a `_BuiltBook` (manifest items, navigation, metadata):
   - Picks a CSS preset (`novel`, `manuscript`) and optionally appends `options.extra["custom_css"]`
   - Attaches cover image bytes if supplied; otherwise renders a plain title page
   - Renders a copyright page when `options.author` or `options.subtitle` is set
   - Attaches every referenced image with the extension derived from a sniffed magic-byte check (`_guess_image_type`) so the OPF media-type matches the bytes on disk
   - Renders chapters as XHTML with first-paragraph drop-cap class, speaker labels for PC/NPC posts, and capped at three inline illustrations per scene
   - Renders appendices: `cast`, `world` (worlds, locations, factions, lore, items), `continuity`, `calendar`, `gallery`, and optionally `prompts`
   - Inserts a navigation document after the title page
4. `_write_epub` writes the OCF zip: `mimetype` stored uncompressed at offset 0, `META-INF/container.xml`, `OEBPS/content.opf`, then every manifest item under `OEBPS/`
5. If `options.extra["validate"]` is truthy **and** `epubcheck_path` was supplied at construction time, `_run_epubcheck` shells out and folds the results into `result.warnings`

`EmptyExportError` is raised if the selection produces zero scenes. EPUBCheck failures never block delivery — they're surfaced as warnings, matching the spec's "validates against EPUBCheck before delivery" intent without making validation a hard prerequisite.

Style presets: `novel` (serif body, drop caps, full-width images) and `manuscript` (monospace, double-spaced, images hidden via `display: none`). Unknown presets fall back to `novel` and emit a snapshot warning.

### Bundled adapter plugins (`backend/bundled_plugins/`)

Five plugins, each a `manifest.yaml` + `plugin.py` + `requirements.txt`:

| Plugin | Class | Output |
|---|---|---|
| `export-markdown` | `MarkdownBundleAdapter` | `.zip` with `README.md` + `scenes/`, `characters/`, `world/`, `images/` |
| `export-single-markdown` | `SingleMarkdownAdapter` | One `.md` file |
| `export-json` | `JsonExportAdapter` | One `.json` document; embeddings excluded by default |
| `export-html` | `HtmlExportAdapter` | One standalone `.html` with inlined default CSS |
| `export-transcript` | `TranscriptAdapter` | Plain `.txt` |

Each plugin reads from disk via `load_fs_snapshot`, applies filters via `filter_context_from_dict`, narrows scenes via `filter_scenes`, and renders without touching the live services. The branch id received in `ExportSelection.branch_id` is split on `:` and the trailing component is treated as the branch name (matching how the conformance suite generates `f"{campaign_id}:main"`).

The bundled plugins implement `option_schema` returning permissive JSON-schema dicts; `default_options` returns an empty-titled `ExportOptions(style_preset="default")`.

## Wiring (`main.py:196-212`)

```python
sources = DataSources(
    scenes=container.scenes,
    characters=container.characters,
    world=container.world,
    continuity=container.continuity,
    images=container.imagegen,
    data_root=data_root,
)
container.export = ExportService(
    sources=sources,
    adapters=container.plugins.export_adapters(),
)
```

`ImageGenService` is duck-typed in as the `ImageSource` (its `list_images` matches the protocol). The EPUB adapter is **not** registered here — at runtime the only adapters available are whatever plugins the registry has loaded. The in-tree `EpubAdapter` is exercised by `tests/export/test_*` and constructed directly by callers that want it; production wiring of EPUB is part of the remaining work.

## HTTP surface (`api/campaigns.py:928-942`)

One route today:

```
POST /campaigns/{campaign_id}/export
{ "adapter_id": ..., "selection": {...}, "options": {...} }
```

It validates the body into `ExportSelection` / `ExportOptions` and calls `export.export(...)`. There is no preview, history, or list-adapters route on the campaigns router. The service exposes those methods; only the HTTP wrapper is missing.

## Conformance suite

`ExportAdapterConformance` (`testing/conformance/export.py`) runs four checks for any adapter that declares `kind = "export_adapter"`:

1. `test_export_produces_nonempty_output` — bytes on disk **or** an in-memory `payload`
2. `test_export_respects_scene_selection` — passing `scene_ids=[]` differentiates from "no filter"
3. `test_export_respects_appendix_selection` — runs an export with `include_appendices=["cast"]`; skips if the adapter declares `capabilities.supports_appendices=False`
4. `test_option_schema_is_dict` — `option_schema()` returns a `dict`

The suite gates plugin install in the registry; every bundled adapter passes it as of the second commit.

## Error handling

- `UnknownAdapterError` — adapter id not registered
- `EmptyExportError` — selection resolved to zero scenes (raised inside EPUB adapter; other adapters return an empty bundle with `scene_count=0`)
- `ValidationFailed` — declared in `errors.py` but currently unused; EPUBCheck issues become warnings instead
- `NotImplementedError` from `ImageSource.list_images` is caught per scene in `build_snapshot` and downgraded to "no images for this scene"
- Plugin-side: missing image files generate a warning and skip the figure rather than producing a dangling `<img>` reference

## Test wiring

`tests/export/conftest.py` ships in-memory `StubScenes`/`StubCharacters`/`StubWorld`/`StubContinuity`/`StubImages`/`StubPCs` and helper factories (`make_scene`, `make_post`, `make_character`, `make_library_location`, `make_fact`, `make_commitment`, `make_image`, `make_sources`). The same fixtures back `test_service.py`, `test_snapshot.py`, `test_epub.py`, and `test_filters.py`. The bundled-plugins suite (`tests/bundled_plugins/test_export_adapters.py`) uses an on-disk fixture under `tests/bundled_plugins/conftest.py` because those adapters read via `load_fs_snapshot`.
