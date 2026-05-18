"""EPUB 3 export adapter.

Pure-Python EPUB packager. We don't depend on `ebooklib` because the v1
package only needs the well-defined OCF/OPF subset: a stored ``mimetype``
file at offset 0, a ``META-INF/container.xml`` pointing at an OPF package
document, a navigation document (``nav.xhtml``), one XHTML file per
chapter, an optional cover, and a single CSS stylesheet.

Pipeline (spec 13 §EPUB adapter):
  1. Resolve every scene + appendix in the selection (delegated to the
     snapshot builder).
  2. Format the front matter, chapters, and appendices as XHTML.
  3. Assemble the OPF package + navigation document.
  4. Zip everything into an ``.epub`` and write it to ``output_path``.

EPUBCheck is invoked when available and ``options.extra['validate']`` is
truthy; the warning is recorded on the ``ExportResult`` either way.
"""

from __future__ import annotations

import html
import re
import shutil
import subprocess
import uuid
import zipfile
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, ClassVar

from grimoire.export.config import EpubAdapterConfig, ExportFiltersConfig
from grimoire.export.errors import EmptyExportError
from grimoire.export.snapshot import CampaignSnapshot, ScenePart, build_snapshot
from grimoire.export.sources import DataSources
from grimoire.scenes.types import Scene
from grimoire.types.characters import Character
from grimoire.types.common import CampaignId, JsonSchema
from grimoire.types.composition import LibraryEntity
from grimoire.types.continuity import Commitment, Fact
from grimoire.types.export import (
    ExportCapabilities,
    ExportOptions,
    ExportResult,
    ExportSelection,
)
from grimoire.types.imagegen import ImageMetadata

# ---------- constants ----------------------------------------------------- #

MIME_TYPE = "application/epub+zip"
OEBPS = "OEBPS"
DEFAULT_APPENDICES = ("cast", "world", "calendar", "gallery")

_NOVEL_CSS = """\
@charset "utf-8";
body { font-family: 'Iowan Old Style', 'Georgia', serif; line-height: 1.55; margin: 0 1em; }
h1, h2, h3 { font-family: 'Garamond', 'Georgia', serif; }
h1.chapter { font-size: 1.8em; margin-top: 3em; text-align: center; }
h2.section { font-size: 1.2em; margin-top: 2em; }
p { text-indent: 1.2em; margin: 0; }
p.first { text-indent: 0; }
p.first::first-letter {
  font-size: 2.6em;
  float: left;
  line-height: 1;
  padding-right: 0.1em;
  font-family: 'Garamond', serif;
}
p.player { font-style: italic; padding-left: 1.5em; border-left: 2px solid #cbb; }
p.narrator-aside { color: #555; font-size: 0.92em; text-align: center; }
figure { margin: 1.5em 0; text-align: center; page-break-inside: avoid; }
figure img { max-width: 100%; height: auto; }
figcaption { font-size: 0.85em; color: #555; }
.mech { font-family: 'Courier New', monospace; font-size: 0.85em; color: #644; }
.mech-noteref { font-family: 'Courier New', monospace; font-size: 0.8em; color: #644;
                text-decoration: none; vertical-align: super; }
section.footnotes { margin-top: 3em; border-top: 1px solid #999; padding-top: 1em;
                    font-size: 0.85em; color: #444; }
section.footnotes aside { margin: 0.4em 0; }
.toc-list { list-style: none; padding-left: 0; }
.toc-list li { margin: 0.4em 0; }
.appendix h2 { margin-top: 2em; border-bottom: 1px solid #999; padding-bottom: 0.2em; }
.appendix dl dt { font-weight: bold; margin-top: 0.8em; }
.appendix dl dd { margin-left: 1em; color: #333; }
.source-attribution { font-size: 0.8em; color: #777; font-weight: normal;
                      font-style: italic; }
"""

_MANUSCRIPT_CSS = """\
@charset "utf-8";
body { font-family: 'Courier New', monospace; line-height: 2; margin: 1in; }
h1, h2, h3 { font-family: 'Courier New', monospace; font-weight: normal; }
h1.chapter { page-break-before: always; font-size: 1.2em; margin-top: 4em; }
h2.section { font-size: 1em; margin-top: 2em; }
p { text-indent: 0.5in; margin: 0; }
p.first { text-indent: 0; }
p.player { font-style: italic; }
p.narrator-aside { font-style: italic; }
figure, img { display: none; }
.mech { color: #644; }
.mech-noteref { color: #644; text-decoration: none; vertical-align: super; }
section.footnotes { margin-top: 2em; }
.toc-list { list-style: none; padding-left: 0; }
.toc-list li { margin: 0.4em 0; }
.appendix h2 { margin-top: 2em; }
.appendix dl dt { font-weight: bold; }
"""

_STYLE_PRESETS = {
    "novel": _NOVEL_CSS,
    "manuscript": _MANUSCRIPT_CSS,
}


def list_style_presets() -> list[str]:
    return list(_STYLE_PRESETS)


# ---------- option / capability helpers ---------------------------------- #


def _default_options(
    title: str = "Untitled Campaign",
    *,
    style_preset: str = "novel",
    include_appendices: list[str] | None = None,
    validate: bool = False,
) -> ExportOptions:
    return ExportOptions(
        title=title,
        style_preset=style_preset,
        extra={
            "include_appendices": list(include_appendices or DEFAULT_APPENDICES),
            "validate": validate,
        },
    )


def _option_schema() -> JsonSchema:
    return {
        "type": "object",
        "properties": {
            "title": {"type": "string"},
            "subtitle": {"type": "string"},
            "author": {"type": "string"},
            "style_preset": {
                "type": "string",
                "enum": list_style_presets(),
                "default": "novel",
            },
            "extra": {
                "type": "object",
                "properties": {
                    "include_mechanics_footnotes": {"type": "boolean", "default": False},
                    "include_prompts_appendix": {"type": "boolean", "default": False},
                    "include_appendices": {
                        "type": "array",
                        "items": {
                            "type": "string",
                            "enum": [
                                "cast",
                                "world",
                                "locations",
                                "lore",
                                "factions",
                                "items",
                                "calendar",
                                "continuity",
                                "gallery",
                            ],
                        },
                    },
                    "validate": {"type": "boolean", "default": False},
                    "custom_css": {"type": "string"},
                    "cover_caption": {"type": "string"},
                    "show_source_attribution": {"type": "boolean", "default": False},
                    "generate_cover": {"type": "boolean", "default": False},
                },
            },
        },
    }


# ---------- file model used by the packager ------------------------------ #


@dataclass(slots=True)
class _ManifestItem:
    id: str
    href: str  # relative to OEBPS/
    media_type: str
    properties: str = ""
    spine: bool = False
    nav: bool = False
    payload: bytes = b""


@dataclass(slots=True)
class _BuiltBook:
    items: list[_ManifestItem] = field(default_factory=list)
    nav_entries: list[tuple[str, str, list[tuple[str, str]]]] = field(default_factory=list)
    metadata: dict[str, str] = field(default_factory=dict)
    identifier: str = ""
    cover_id: str | None = None


# ---------- adapter ------------------------------------------------------- #


class EpubAdapter:
    """``ExportAdapter`` implementation for EPUB 3."""

    id: ClassVar[str] = "epub"
    name: ClassVar[str] = "EPUB 3"
    extensions: ClassVar[list[str]] = ["epub"]
    mime_type: ClassVar[str] = MIME_TYPE
    capabilities: ClassVar[ExportCapabilities] = ExportCapabilities(
        supports_images=True,
        supports_appendices=True,
        supports_filters=True,
        supported_style_presets=["novel", "manuscript"],
    )

    def __init__(
        self,
        sources: DataSources,
        *,
        epubcheck_path: str | Path | None = None,
        clock: Any = None,
        config: EpubAdapterConfig | None = None,
        filter_defaults: ExportFiltersConfig | None = None,
    ) -> None:
        self.sources = sources
        self.config = config or EpubAdapterConfig()
        self._filter_defaults = filter_defaults
        # CLI / explicit arg wins over config to preserve the previous test API.
        resolved_path = epubcheck_path or self.config.epubcheck_path
        self.epubcheck_path = str(resolved_path) if resolved_path else None
        self._clock = clock or (lambda: datetime.now(UTC))

    # -- ExportAdapter surface ------------------------------------------- #

    def default_options(self) -> ExportOptions:
        return _default_options(
            style_preset=self.config.default_style,
            include_appendices=list(self.config.include_appendices_by_default),
            validate=self.config.validate_with_epubcheck,
        )

    def option_schema(self) -> JsonSchema:
        return _option_schema()

    async def export(
        self,
        campaign_id: CampaignId,
        selection: ExportSelection,
        options: ExportOptions,
        output_path: Path,
    ) -> ExportResult:
        # Make sure the appendix selection from options.extra reaches the
        # snapshot builder. Callers can also drop them into selection.include
        # _appendices directly; the union wins.
        merged_selection = _merge_appendix_choices(selection, options)
        snapshot = await build_snapshot(
            campaign_id,
            merged_selection,
            options,
            self.sources,
            filter_defaults=self._filter_defaults,
        )
        if not snapshot.scenes:
            raise EmptyExportError(
                f"campaign {campaign_id!r}: selection produced no exportable scenes"
            )

        snapshot.options = await self._maybe_generate_cover(
            campaign_id, options, snapshot
        )

        book = _build_book(snapshot, self._clock())
        _write_epub(output_path, book)

        warnings = list(snapshot.warnings)
        if (options.extra or {}).get("validate") and self.epubcheck_path:
            warnings.extend(_run_epubcheck(self.epubcheck_path, output_path))

        return ExportResult(
            format="epub",
            size_bytes=output_path.stat().st_size,
            scene_count=snapshot.scene_count,
            word_count=snapshot.word_count,
            image_count=snapshot.image_count,
            file_path=str(output_path),
            warnings=warnings,
            created_at=self._clock(),
        )

    async def _maybe_generate_cover(
        self,
        campaign_id: CampaignId,
        options: ExportOptions,
        snapshot: CampaignSnapshot,
    ) -> ExportOptions:
        """Render a cover via ImageGen when opted in and none was supplied.

        Off by default; gated on ``options.extra['generate_cover']`` or the
        per-adapter ``default_cover_generated`` config knob. Returns the
        possibly-updated ``options`` (with ``cover_image`` populated).
        """

        if options.cover_image:
            return options
        extra = options.extra or {}
        opt_in = bool(extra.get("generate_cover", self.config.default_cover_generated))
        if not opt_in:
            return options
        generator = getattr(self.sources, "cover_generator", None)
        if generator is None:
            snapshot.warnings.append(
                "generate_cover requested but no cover generator is wired"
            )
            return options
        prompt = _cover_prompt(options, snapshot)
        try:
            payload = await generator.generate_cover(campaign_id, prompt)
        except Exception as exc:  # tolerate any backend failure
            snapshot.warnings.append(f"cover generation failed: {exc!r}")
            return options
        if not payload:
            snapshot.warnings.append("cover generation returned no image")
            return options
        return options.model_copy(update={"cover_image": payload})


def _cover_prompt(options: ExportOptions, snapshot: CampaignSnapshot) -> str:
    """Compose a one-line cover prompt from campaign metadata + first chapter."""
    bits: list[str] = []
    if options.title:
        bits.append(options.title)
    if options.subtitle:
        bits.append(options.subtitle)
    if snapshot.worlds:
        world = snapshot.worlds[0]
        if world.genre:
            bits.append(world.genre)
        if world.description:
            bits.append(world.description[:160])
    if snapshot.scenes:
        first = snapshot.scenes[0].scene
        if first.title:
            bits.append(f"opening scene: {first.title}")
        if first.mood:
            bits.append(f"mood: {first.mood}")
    bits.append("book cover illustration, dramatic lighting")
    return " — ".join(b for b in bits if b)


# ---------- selection helpers -------------------------------------------- #


def _merge_appendix_choices(selection: ExportSelection, options: ExportOptions) -> ExportSelection:
    extra = options.extra or {}
    chosen = list(selection.include_appendices or [])
    for name in extra.get("include_appendices", DEFAULT_APPENDICES) or []:
        if name not in chosen:
            chosen.append(name)
    return selection.model_copy(update={"include_appendices": chosen})


# ---------- builders ----------------------------------------------------- #


def _build_book(snapshot: CampaignSnapshot, now: datetime) -> _BuiltBook:
    options = snapshot.options
    identifier = (options.extra or {}).get("uuid") or f"urn:uuid:{uuid.uuid4()}"
    style_preset = options.style_preset or "novel"
    if style_preset not in _STYLE_PRESETS:
        snapshot.warnings.append(f"unknown style preset {style_preset!r}; falling back to 'novel'")
        style_preset = "novel"
    custom_css = (options.extra or {}).get("custom_css", "")
    css = _STYLE_PRESETS[style_preset]
    if custom_css:
        css = css + "\n/* user css */\n" + str(custom_css)

    book = _BuiltBook(identifier=identifier)
    book.metadata.update(
        {
            "title": options.title or "Untitled Campaign",
            "subtitle": options.subtitle or "",
            "author": options.author or "Unknown",
            "language": (options.extra or {}).get("language", "en"),
            "modified": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
    )

    # Stylesheet ------------------------------------------------------------
    css_item = _ManifestItem(
        id="style-main",
        href="styles/main.css",
        media_type="text/css",
        payload=css.encode("utf-8"),
    )
    book.items.append(css_item)

    # Cover -----------------------------------------------------------------
    if options.cover_image:
        cover_image_id = _add_cover_image(book, options.cover_image)
        book.cover_id = cover_image_id
        cover_xhtml = _ManifestItem(
            id="cover-xhtml",
            href="cover.xhtml",
            media_type="application/xhtml+xml",
            spine=True,
            properties="",
            payload=_render_cover_page(options, cover_image_id, book).encode("utf-8"),
        )
        book.items.append(cover_xhtml)

    # Title page ------------------------------------------------------------
    title_item = _ManifestItem(
        id="title-page",
        href="title.xhtml",
        media_type="application/xhtml+xml",
        spine=True,
        payload=_render_title_page(options).encode("utf-8"),
    )
    book.items.append(title_item)

    # Front-matter chapters -------------------------------------------------
    if options.author or options.subtitle:
        front = _ManifestItem(
            id="front-copyright",
            href="frontmatter/copyright.xhtml",
            media_type="application/xhtml+xml",
            spine=True,
            payload=_render_copyright_page(options, now).encode("utf-8"),
        )
        book.items.append(front)

    # Images: attach every referenced image up front so chapter / gallery
    # rendering can resolve each one to the actual href written to the zip
    # (extension is derived from sniffed bytes, not assumed to be .png).
    image_hrefs: dict[str, str] = {}
    for part in snapshot.scenes:
        for img in part.images:
            attached = _attach_image(book, img, snapshot.data_root)
            if attached is not None:
                image_hrefs[img.id] = attached
    for img in snapshot.images:
        if img.id in image_hrefs:
            continue
        attached = _attach_image(book, img, snapshot.data_root)
        if attached is not None:
            image_hrefs[img.id] = attached

    # Chapters --------------------------------------------------------------
    chapter_items: list[_ManifestItem] = []
    for index, part in enumerate(snapshot.scenes, start=1):
        chapter_id = f"chapter-{index:04d}"
        href = f"chapters/{chapter_id}.xhtml"
        xhtml = _render_chapter(index, part, options, image_hrefs)
        chapter_items.append(
            _ManifestItem(
                id=chapter_id,
                href=href,
                media_type="application/xhtml+xml",
                spine=True,
                payload=xhtml.encode("utf-8"),
            )
        )
    book.items.extend(chapter_items)

    # Appendices ------------------------------------------------------------
    appendix_items = _render_appendices(snapshot, options, image_hrefs)
    book.items.extend(appendix_items)

    # Nav document ----------------------------------------------------------
    nav_payload = _render_nav(snapshot, chapter_items, appendix_items)
    nav_item = _ManifestItem(
        id="nav",
        href="nav.xhtml",
        media_type="application/xhtml+xml",
        properties="nav",
        spine=True,
        nav=True,
        payload=nav_payload.encode("utf-8"),
    )
    # Put nav early in the spine after title page so readers find it quickly.
    insert_at = next((i for i, item in enumerate(book.items) if item.id == "title-page"), 0) + 1
    book.items.insert(insert_at, nav_item)

    return book


def _add_cover_image(book: _BuiltBook, payload: bytes) -> str:
    media_type = _guess_image_type(payload)
    href = "images/cover" + _extension_for(media_type)
    item = _ManifestItem(
        id="cover-image",
        href=href,
        media_type=media_type,
        properties="cover-image",
        payload=payload,
    )
    book.items.append(item)
    return item.id


def _attach_image(book: _BuiltBook, image: ImageMetadata, data_root: Path | None) -> str | None:
    """Add an image to the package and return the href it was written to.

    Returns ``None`` when the underlying file is missing so callers can
    skip rendering a broken ``<img>`` tag instead of pointing at a path
    that doesn't exist in the zip.
    """

    payload = _read_image_bytes(image, data_root)
    if payload is None:
        return None
    media_type = _guess_image_type(payload, fallback="image/png")
    href = f"images/{image.id}{_extension_for(media_type)}"
    if any(item.href == href for item in book.items):
        return href
    item_id = f"img-{image.id}"
    book.items.append(
        _ManifestItem(
            id=item_id,
            href=href,
            media_type=media_type,
            payload=payload,
        )
    )
    return href


def _read_image_bytes(image: ImageMetadata, data_root: Path | None) -> bytes | None:
    candidate: Path | None = None
    if image.file_path:
        path = Path(image.file_path)
        if not path.is_absolute() and data_root is not None:
            path = data_root / image.file_path
        candidate = path
    if candidate is None or not candidate.exists():
        return None
    try:
        return candidate.read_bytes()
    except OSError:
        return None


def _guess_image_type(payload: bytes, *, fallback: str = "image/png") -> str:
    if payload.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if payload.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if payload[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    if payload.startswith(b"RIFF") and payload[8:12] == b"WEBP":
        return "image/webp"
    return fallback


def _extension_for(media_type: str) -> str:
    return {
        "image/png": ".png",
        "image/jpeg": ".jpg",
        "image/gif": ".gif",
        "image/webp": ".webp",
    }.get(media_type, ".bin")


# ---------- chapter & appendix rendering --------------------------------- #


def _xml_decl() -> str:
    return '<?xml version="1.0" encoding="utf-8"?>\n'


def _xhtml_doc(title: str, body: str, *, stylesheet: str = "styles/main.css") -> str:
    return (
        _xml_decl()
        + '<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops" '
        + 'lang="en">\n'
        + "<head>\n"
        + f"  <title>{html.escape(title)}</title>\n"
        + '  <meta charset="utf-8"/>\n'
        + f'  <link rel="stylesheet" type="text/css" href="{stylesheet}"/>\n'
        + "</head>\n"
        + "<body>\n"
        + body
        + "\n</body>\n</html>\n"
    )


def _format_paragraphs(
    body: str,
    *,
    css_class: str | None = None,
    footnote_collector: list[tuple[str, str]] | None = None,
    chapter_index: int = 0,
) -> str:
    """Render paragraphs to XHTML.

    When ``footnote_collector`` is supplied, ``[roll …]`` / ``[mech …]`` /
    ``[stat …]`` chips inside the body are extracted, replaced inline with
    EPUB ``<a epub:type="noteref">`` refs, and their content appended to the
    list as ``(note_id, body_text)`` pairs. The caller is expected to render
    the matching ``<aside epub:type="footnote">`` blocks at chapter end.
    """

    paragraphs = [p.strip() for p in body.split("\n\n") if p.strip()]
    out: list[str] = []
    for index, paragraph in enumerate(paragraphs):
        klass = []
        if index == 0:
            klass.append("first")
        if css_class:
            klass.append(css_class)
        attr = f' class="{" ".join(klass)}"' if klass else ""
        rendered = _render_inline_text(paragraph, footnote_collector, chapter_index)
        out.append(f"<p{attr}>{rendered}</p>")
    return "\n".join(out)


_MECH_CHIP_RE = re.compile(
    r"\[\s*(?P<kind>roll|mech|stat|sheet|result)\s*[:\-—]?\s*(?P<body>[^\]]*)\]",
    re.IGNORECASE,
)


def _render_inline_text(
    text: str,
    footnote_collector: list[tuple[str, str]] | None,
    chapter_index: int,
) -> str:
    if footnote_collector is None:
        return html.escape(text).replace("\n", "<br/>")

    parts: list[str] = []
    last = 0
    for match in _MECH_CHIP_RE.finditer(text):
        parts.append(html.escape(text[last : match.start()]).replace("\n", "<br/>"))
        kind = match.group("kind").lower()
        body = match.group("body").strip() or kind
        note_index = len(footnote_collector) + 1
        note_id = f"fn-ch{chapter_index}-{note_index}"
        anchor_id = f"{note_id}-ref"
        parts.append(
            f'<a id="{anchor_id}" href="#{note_id}" epub:type="noteref" '
            f'class="mech-noteref">[{html.escape(kind)}]</a>'
        )
        footnote_collector.append((note_id, body))
        last = match.end()
    parts.append(html.escape(text[last:]).replace("\n", "<br/>"))
    return "".join(parts)


def _scene_header(index: int, scene: Scene) -> str:
    title = html.escape(scene.title or f"Scene {scene.ordinal}")
    chapter_number = html.escape(f"Chapter {index}")
    location = html.escape(scene.location_ref or "")
    moment = scene.in_game_start.strftime("%Y-%m-%d %H:%M") if scene.in_game_start else ""
    parts = [
        f'<h1 class="chapter" id="ch{index}-title"><span class="num">{chapter_number}</span> '
        f"<br/>{title}</h1>"
    ]
    if location or moment:
        bits = [b for b in (location, html.escape(moment)) if b]
        parts.append('<p class="narrator-aside">' + " · ".join(bits) + "</p>")
    return "\n".join(parts)


def _render_chapter(
    index: int,
    part: ScenePart,
    options: ExportOptions,
    image_hrefs: dict[str, str],
) -> str:
    body_parts: list[str] = [_scene_header(index, part.scene)]
    include_mech = bool((options.extra or {}).get("include_mechanics_footnotes", False))
    footnotes: list[tuple[str, str]] | None = [] if include_mech else None
    for img in part.images[:3]:  # cap inline illustrations per spec
        href = image_hrefs.get(img.id)
        if href:
            body_parts.append(
                f'<figure><img alt="{html.escape(img.prompt or img.id)}" src="../{href}"/></figure>'
            )
    for post in part.posts:
        css = None
        if post.author_kind == "pc":
            css = "player"
        elif post.author_kind in ("narrator", "system"):
            css = "narrator-aside" if post.author_kind == "system" else None
        elif post.author_kind == "npc":
            css = None
        prefix = ""
        if post.author_kind in ("pc", "npc"):
            label = html.escape(post.author_display)
            prefix = f'<p class="speaker"><strong>{label}:</strong></p>\n'
        body_parts.append(
            prefix
            + _format_paragraphs(
                post.body,
                css_class=css,
                footnote_collector=footnotes,
                chapter_index=index,
            )
        )

    if footnotes:
        body_parts.append('<section epub:type="footnotes" class="footnotes">')
        for note_id, body in footnotes:
            body_parts.append(
                f'<aside id="{note_id}" epub:type="footnote">'
                f'<p>{html.escape(body)} '
                f'<a href="#{note_id}-ref" epub:type="backlink">↩</a></p>'
                "</aside>"
            )
        body_parts.append("</section>")

    return _xhtml_doc(
        part.scene.title or f"Chapter {index}",
        "\n".join(body_parts),
        stylesheet="../styles/main.css",
    )


def _render_title_page(options: ExportOptions) -> str:
    parts = [
        f'<h1 class="chapter">{html.escape(options.title or "Untitled")}</h1>',
    ]
    if options.subtitle:
        parts.append(f'<p class="narrator-aside">{html.escape(options.subtitle)}</p>')
    if options.author:
        parts.append(f'<p class="narrator-aside">by {html.escape(options.author)}</p>')
    return _xhtml_doc("Title", "\n".join(parts))


def _render_cover_page(options: ExportOptions, image_id: str, book: _BuiltBook) -> str:
    item = next((i for i in book.items if i.id == image_id), None)
    href = item.href if item else "images/cover.png"
    caption = (options.extra or {}).get("cover_caption", "") or (options.title or "")
    body = (
        f'<figure><img alt="{html.escape(options.title or "Cover")}" src="{href}"/>'
        f"<figcaption>{html.escape(caption)}</figcaption></figure>"
    )
    return _xhtml_doc("Cover", body)


def _render_copyright_page(options: ExportOptions, now: datetime) -> str:
    parts = [
        '<h2 class="section">Copyright</h2>',
        f"<p>© {now.year} {html.escape(options.author or 'the author')}.</p>",
        "<p>This volume was generated by Grimoire.</p>",
    ]
    return _xhtml_doc("Copyright", "\n".join(parts))


def _render_appendices(
    snapshot: CampaignSnapshot,
    options: ExportOptions,
    image_hrefs: dict[str, str],
) -> list[_ManifestItem]:
    items: list[_ManifestItem] = []
    appendices = set(snapshot.selection.include_appendices)

    if "cast" in appendices and snapshot.characters:
        items.append(_appendix_item("cast", "Cast", _render_cast(snapshot.characters)))
    if any(name in appendices for name in ("world", "locations", "lore", "factions", "items")):
        show_attribution = bool(
            (options.extra or {}).get("show_source_attribution", False)
        )
        body = _render_world_appendix(
            snapshot, appendices, show_attribution=show_attribution
        )
        if body:
            items.append(_appendix_item("world", "World", body))
    if "continuity" in appendices and (snapshot.facts or snapshot.commitments):
        items.append(
            _appendix_item(
                "continuity",
                "Continuity Ledger",
                _render_continuity(snapshot.facts, snapshot.commitments),
            )
        )
    if "calendar" in appendices and snapshot.scenes:
        items.append(_appendix_item("calendar", "Calendar", _render_calendar(snapshot.scenes)))
    if "gallery" in appendices and snapshot.images:
        items.append(
            _appendix_item(
                "gallery", "Image Gallery", _render_gallery(snapshot.images, image_hrefs)
            )
        )
    if (options.extra or {}).get("include_prompts_appendix") and snapshot.images:
        items.append(
            _appendix_item(
                "prompts",
                "Image Prompts",
                _render_prompts_appendix(snapshot.images),
            )
        )
    return items


def _appendix_item(slug: str, title: str, body_html: str) -> _ManifestItem:
    wrapped = '<section class="appendix">\n' + body_html + "\n</section>\n"
    payload = _xhtml_doc(
        title,
        f'<h1 class="chapter">{html.escape(title)}</h1>\n' + wrapped,
        stylesheet="../styles/main.css",
    )
    return _ManifestItem(
        id=f"appendix-{slug}",
        href=f"appendices/{slug}.xhtml",
        media_type="application/xhtml+xml",
        spine=True,
        payload=payload.encode("utf-8"),
    )


def _render_cast(characters: Iterable[Character]) -> str:
    rows: list[str] = ["<dl>"]
    for character in characters:
        name = html.escape(character.name)
        role = html.escape(getattr(character.role, "value", str(character.role)))
        desc = html.escape((character.description or "").strip() or "—")
        rows.append(f"<dt>{name} <em>({role})</em></dt>")
        rows.append(f"<dd>{desc}</dd>")
    rows.append("</dl>")
    return "\n".join(rows)


def _render_world_appendix(
    snapshot: CampaignSnapshot,
    appendices: set[str],
    *,
    show_attribution: bool = False,
) -> str:
    world_versions = {w.id: w.version for w in snapshot.worlds}
    parts: list[str] = []
    if snapshot.worlds:
        parts.append("<h2>Worlds</h2><ul>")
        for st in snapshot.worlds:
            attribution = (
                f' <span class="source-attribution">&lt;source: {html.escape(st.id)} '
                f"v{st.version}&gt;</span>"
                if show_attribution
                else ""
            )
            parts.append(
                f"<li><strong>{html.escape(st.name)}</strong> — "
                f"{html.escape(st.description or st.genre or '')}{attribution}</li>"
            )
        parts.append("</ul>")
    for kind, label, items in (
        ("locations", "Locations", snapshot.locations),
        ("factions", "Factions", snapshot.factions),
        ("lore", "Lore", snapshot.lore),
        ("items", "Items", snapshot.items),
    ):
        if (kind in appendices or "world" in appendices) and items:
            parts.append(f"<h2>{label}</h2>")
            parts.append(
                _render_library_dl(
                    items,
                    show_attribution=show_attribution,
                    world_versions=world_versions,
                )
            )
    return "\n".join(parts)


def _render_library_dl(
    entries: Iterable[LibraryEntity],
    *,
    show_attribution: bool = False,
    world_versions: dict[str, int] | None = None,
) -> str:
    versions = world_versions or {}
    rows = ["<dl>"]
    for entity in entries:
        name = html.escape(entity.name)
        body = html.escape((entity.body or "").strip().split("\n\n", 1)[0] or "—")
        attribution = ""
        if show_attribution and entity.world_id:
            version = versions.get(entity.world_id)
            label = f"{entity.world_id} v{version}" if version is not None else entity.world_id
            attribution = (
                f' <span class="source-attribution">&lt;source: '
                f"{html.escape(label)}&gt;</span>"
            )
        rows.append(f"<dt>{name}{attribution}</dt><dd>{body}</dd>")
    rows.append("</dl>")
    return "\n".join(rows)


def _render_continuity(facts: list[Fact], commitments: list[Commitment]) -> str:
    parts: list[str] = []
    if facts:
        parts.append("<h2>Facts</h2><ul>")
        for fact in facts:
            parts.append(
                f"<li>{html.escape(fact.text)} <em>(confidence {fact.confidence:.2f})</em></li>"
            )
        parts.append("</ul>")
    if commitments:
        parts.append("<h2>Commitments</h2><ul>")
        for c in commitments:
            kind = getattr(c.kind, "value", str(c.kind))
            status = getattr(c.status, "value", str(c.status))
            parts.append(
                f"<li><strong>[{html.escape(kind)}]</strong> {html.escape(c.text)} "
                f"<em>({html.escape(status)})</em></li>"
            )
        parts.append("</ul>")
    return "\n".join(parts)


def _render_calendar(scenes: list[ScenePart]) -> str:
    rows: list[str] = ['<ul class="toc-list">']
    for part in scenes:
        moment = (
            part.scene.in_game_start.strftime("%Y-%m-%d %H:%M")
            if part.scene.in_game_start
            else "unscheduled"
        )
        rows.append(
            f"<li><strong>{html.escape(moment)}</strong> — "
            f"{html.escape(part.scene.title or part.scene.slug)}</li>"
        )
    rows.append("</ul>")
    return "\n".join(rows)


def _render_gallery(images: list[ImageMetadata], image_hrefs: dict[str, str]) -> str:
    rows = ['<div class="gallery">']
    for img in images:
        href = image_hrefs.get(img.id)
        if not href:
            # Image bytes were unreadable; skip the figure rather than emit
            # a dangling reference that EPUBCheck (and readers) will flag.
            continue
        rows.append(
            f'<figure><img alt="{html.escape(img.prompt or img.id)}" '
            f'src="../{href}"/>'
            f"<figcaption>{html.escape(img.prompt or '')}</figcaption></figure>"
        )
    rows.append("</div>")
    return "\n".join(rows)


def _render_prompts_appendix(images: list[ImageMetadata]) -> str:
    rows = ["<dl>"]
    for img in images:
        rows.append(f"<dt>{html.escape(img.id)}</dt>")
        rows.append(f"<dd>{html.escape(img.prompt or '—')}</dd>")
    rows.append("</dl>")
    return "\n".join(rows)


def _render_nav(
    snapshot: CampaignSnapshot,
    chapter_items: list[_ManifestItem],
    appendix_items: list[_ManifestItem],
) -> str:
    nav: list[str] = ['<nav epub:type="toc" id="toc"><h1>Contents</h1><ol class="toc-list">']
    for index, (part, item) in enumerate(zip(snapshot.scenes, chapter_items, strict=False), 1):
        title = html.escape(part.scene.title or f"Chapter {index}")
        nav.append(f'<li><a href="{item.href}">{title}</a></li>')
    if appendix_items:
        nav.append("<li><span>Appendices</span><ol>")
        for item in appendix_items:
            slug = item.id.removeprefix("appendix-")
            label = slug.replace("-", " ").title()
            nav.append(f'<li><a href="{item.href}">{html.escape(label)}</a></li>')
        nav.append("</ol></li>")
    nav.append("</ol></nav>")
    return _xhtml_doc("Contents", "\n".join(nav))


# ---------- packaging ---------------------------------------------------- #


def _write_epub(output_path: Path, book: _BuiltBook) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    # Stable ordering for reproducible bundles.
    items = list(book.items)
    spine = [item for item in items if item.spine]

    with zipfile.ZipFile(output_path, "w") as zf:
        # mimetype must be the first entry, stored (not deflated), no extras.
        zf.writestr(zipfile.ZipInfo("mimetype"), MIME_TYPE, compress_type=zipfile.ZIP_STORED)

        zf.writestr("META-INF/container.xml", _container_xml())
        zf.writestr(f"{OEBPS}/content.opf", _content_opf(book, items, spine))

        for item in items:
            zf.writestr(f"{OEBPS}/{item.href}", item.payload)


def _container_xml() -> str:
    return (
        _xml_decl()
        + '<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">\n'
        + "  <rootfiles>\n"
        + f'    <rootfile full-path="{OEBPS}/content.opf" '
        + 'media-type="application/oebps-package+xml"/>\n'
        + "  </rootfiles>\n"
        + "</container>\n"
    )


def _content_opf(book: _BuiltBook, items: list[_ManifestItem], spine: list[_ManifestItem]) -> str:
    metadata = book.metadata
    manifest_entries = []
    for item in items:
        properties = f' properties="{item.properties}"' if item.properties else ""
        manifest_entries.append(
            f'    <item id="{item.id}" href="{item.href}" '
            f'media-type="{item.media_type}"{properties}/>'
        )
    spine_entries = [f'    <itemref idref="{item.id}"/>' for item in spine]

    subtitle = (
        f"  <dc:description>{html.escape(metadata['subtitle'])}</dc:description>\n"
        if metadata.get("subtitle")
        else ""
    )
    meta_cover = f'  <meta name="cover" content="{book.cover_id}"/>\n' if book.cover_id else ""

    return (
        _xml_decl()
        + '<package version="3.0" xmlns="http://www.idpf.org/2007/opf" unique-identifier="bookid"'
        + ' xml:lang="'
        + html.escape(metadata.get("language", "en"))
        + '">\n'
        + '  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">\n'
        + f'    <dc:identifier id="bookid">{html.escape(book.identifier)}</dc:identifier>\n'
        + f"    <dc:title>{html.escape(metadata.get('title', ''))}</dc:title>\n"
        + f"    <dc:creator>{html.escape(metadata.get('author', 'Unknown'))}</dc:creator>\n"
        + f"    <dc:language>{html.escape(metadata.get('language', 'en'))}</dc:language>\n"
        + f'    <meta property="dcterms:modified">{metadata["modified"]}</meta>\n'
        + subtitle
        + meta_cover
        + "  </metadata>\n"
        + "  <manifest>\n"
        + "\n".join(manifest_entries)
        + "\n"
        + "  </manifest>\n"
        + "  <spine>\n"
        + "\n".join(spine_entries)
        + "\n"
        + "  </spine>\n"
        + "</package>\n"
    )


# ---------- validation --------------------------------------------------- #


def _run_epubcheck(epubcheck_path: str, output_path: Path) -> list[str]:
    if not shutil.which(epubcheck_path) and not Path(epubcheck_path).exists():
        return [f"EPUBCheck binary {epubcheck_path!r} not found; skipped validation"]
    try:
        result = subprocess.run(
            [epubcheck_path, str(output_path)],
            check=False,
            capture_output=True,
            text=True,
            timeout=60,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return [f"EPUBCheck invocation failed: {exc!r}"]
    if result.returncode == 0:
        return []
    lines = (result.stdout or "").splitlines() + (result.stderr or "").splitlines()
    summary = " | ".join(line.strip() for line in lines if line.strip())[:512]
    return [f"EPUBCheck reported issues (exit {result.returncode}): {summary}"]


__all__ = [
    "EpubAdapter",
    "list_style_presets",
]
