# 13 — Export

## Purpose

The Export module produces shareable, archivable, or printable artifacts from a campaign: EPUB books, markdown transcripts, JSON dumps, HTML pages. Like Mechanics and ImageGen, it is plugin-based — each format is an adapter conforming to a common interface. EPUB is the priority for v1.

Export reads everything through the campaign's composition: character cards in appendices come from the resolved cast (library assets + campaign-local), location descriptions from resolved world, prose from campaign-local posts. Source attribution is optional in output (most users want the polished read; some want "from `wod-london` v7" labels on appendix entries).

## Responsibilities

- Maintain a registry of export format adapters (each is a plugin)
- Run export pipelines: gather state from composition, transform, emit
- Provide selection UI hooks (export range, scenes included, content filters)
- Handle assets (images, fonts, stylesheets) per format
- Support full-campaign exports and per-scene / per-arc exports
- Coordinate with State Store, Characters, Setting, Continuity for content (cascading through composition)
- Track export history (what was exported, when, with what settings, against what library versions)
- Allow user to choose: include library content (full character cards, full setting lore), or limit to campaign-local content

## Non-responsibilities

- Does not generate new prose (consumes existing posts and summaries)
- Does not regenerate images (uses existing assets from ImageGen)
- Does not modify state (read-only operation on the State Store)
- Does not export library assets directly (that's a library-level operation; export is campaign-centric)

## Format adapter interface

```python
class ExportAdapter(Protocol):
    id: str
    name: str
    extensions: list[str]
    mime_type: str
    capabilities: ExportCapabilities

    async def export(
        self,
        campaign_id: str,
        selection: ExportSelection,
        options: ExportOptions,
    ) -> ExportResult: ...

    def default_options(self) -> ExportOptions: ...
    def option_schema(self) -> JsonSchema: ...

@dataclass
class ExportSelection:
    branch_id: str
    scene_ids: Optional[list[str]]
    date_range: Optional[tuple[InGameTime, InGameTime]]
    include_images: bool
    include_appendices: list[str]
    include_drafts: bool
    include_review_queue: bool

@dataclass
class ExportOptions:
    title: str
    subtitle: Optional[str]
    author: Optional[str]
    cover_image: Optional[bytes]
    style_preset: str
    extra: dict
```

## Built-in adapters (v1)

| Adapter | Format | Notes |
|---|---|---|
| epub | EPUB 3 | Primary format. Cover, TOC, scenes as chapters, images, appendices |
| markdown | Bundle of .md files | One file per scene; index file; assets in subdirectory |
| single_markdown | One concatenated .md | For quick share |
| json | Full structured dump | For backup or migration |
| transcript | Plain-text transcript | Just the prose, minimally formatted |
| html | Standalone HTML | For web hosting / archival |

## EPUB adapter (priority)

Pipeline:

1. Fetch campaign, branch, and all scenes in selection
2. Generate front matter (title page, copyright, TOC, dedication if set)
3. For each scene:
   - Chapter heading (auto from scene title or user-edited)
   - Inline scene illustrations at the start of the chapter
   - Format posts: player as block quotes or styled paragraphs, model responses as main prose, mechanical events as inline annotations or footnotes (user choice)
   - Emit chapter as XHTML
4. Generate appendices:
   - Cast: character list with capsules
   - Setting: locations and lore
   - Continuity: facts ledger (optional; often spoils plot)
   - Calendar: timeline of major events
   - Image gallery (optional)
5. Bundle into EPUB 3 package with stylesheet

EPUB style is configurable; ships with two presets:
- **Novel**: serif body, drop caps on chapter start, image full-width
- **Manuscript**: monospace, double-spaced, no images, minimal styling (for editing)

Custom CSS can be supplied. EPUB validates against EPUBCheck before delivery.

## Markdown bundle

Produces a directory tree:

```
campaign-name/
  README.md
  scenes/
    001-arrival.md
    002-the-letter.md
  characters/
    julian.md
    winifred.md
  setting/
    locations.md
    factions.md
    lore.md
  continuity/
    facts.md
    commitments.md
  images/
    *.png
```

Index file cross-links scenes and appendix entries. Markdown is plain CommonMark.

## Filter and transformation hooks

Users want to control what goes into the export:
- Strip narrator scaffolding (`[scene break]`, `(julian rolls)`, etc.)
- Strip mechanical events (keep prose only)
- Strip OOC asides
- Anonymize PC name with a chosen pseudonym
- Content filter: skip scenes tagged with specific tags
- POV consolidation: merge POV switches into a unified narrative

Each adapter exposes the filters it supports. Filters are applied during a transformation pass over the prose before formatting.

## Asset handling

Images, custom fonts, and stylesheets are bundled per format:
- EPUB: assets go in OEBPS/images and OEBPS/styles
- Markdown: assets in images/, referenced relatively
- HTML: assets in assets/, referenced relatively
- JSON: images embedded as base64 or referenced by path

ImageGen-produced images carry metadata (prompt, seed). The EPUB optionally includes a prompts appendix.

## Per-scene and per-arc exports

For sharing or printing a single arc, the user can:
- Select scenes by date range
- Select scenes by tag
- Select scenes by user-marked "arc"
- Custom multi-select via UI

Scene Manager supports marking scenes as part of an arc; Export uses these as selection units.

## Interface

```python
class Export(Protocol):
    def list_adapters(self) -> list[ExportAdapter]: ...
    def get_adapter(self, id: str) -> ExportAdapter: ...

    async def export(
        self,
        campaign_id: str,
        adapter_id: str,
        selection: ExportSelection,
        options: ExportOptions,
    ) -> ExportResult: ...

    async def preview(
        self,
        campaign_id: str,
        adapter_id: str,
        selection: ExportSelection,
        options: ExportOptions,
    ) -> ExportPreview: ...

    async def history(self, campaign_id: str) -> list[ExportRecord]: ...

@dataclass
class ExportResult:
    file_path: Optional[str]
    bytes: Optional[bytes]
    size_bytes: int
    format: str
    scene_count: int
    word_count: int
    image_count: int
    warnings: list[str]
    created_at: datetime
```

## Configuration

```yaml
export:
  default_adapter: epub
  output_directory: ./exports
  adapters:
    epub:
      default_style: novel
      include_appendices_by_default:
        - cast
        - setting
        - calendar
      validate_with_epubcheck: true
      default_cover_generated: true
    markdown:
      default_filename_format: "{scene_number:03d}-{title_slug}.md"
      include_assets: true
    json:
      pretty_print: true
      include_embeddings: false
  filters:
    strip_ooc_default: true
    strip_mechanics_default: false
    anonymize_default: false
```

## Open questions

- **Print-ready PDF.** Useful for hard copies. The PDF adapter could wrap EPUB-to-PDF via Calibre or Pandoc. Likely v2.
- **Multi-volume exports.** For 200-session campaigns, single EPUB is unwieldy. Auto-split by arc, by date range, or by user breakpoint.
- **Continuity in exports.** Continuity ledger contains spoilers. Default: include as appendix in personal exports, exclude in "share" exports.
- **Cover generation.** Auto-generated covers via ImageGen. Off by default; nice-to-have.
- **Incremental exports.** Export "new content since last export" as a chapter pack. Adapter-level support for incremental exports.
