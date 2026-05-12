"""Standalone HTML export adapter."""

from __future__ import annotations

import html
import re
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, ClassVar

from grimoire.export import (
    FilterContext,
    anonymize_label,
    apply_filters,
    filter_scenes,
    load_fs_snapshot,
    word_count,
)
from grimoire.export.selection import filter_context_from_dict
from grimoire.types.export import ExportOptions, ExportResult, ExportSelection

_DEFAULT_CSS = """\
body {
  font-family: Georgia, serif; max-width: 42rem; margin: 2rem auto;
  padding: 0 1rem; line-height: 1.55; color: #222;
}
header h1 { margin-bottom: 0.25rem; }
header .meta { color: #666; font-size: 0.9rem; }
nav.toc ol { padding-left: 1.2rem; }
section.scene { margin-top: 3rem; }
section.scene h2 { border-bottom: 1px solid #ccc; padding-bottom: 0.25rem; }
.post { margin: 0.75rem 0; }
.post .author { font-weight: bold; }
.post.pc .author { color: #225; }
.post.npc .author { color: #522; font-style: italic; }
.post.narrator { color: #444; }
.summary { font-style: italic; color: #555; border-left: 3px solid #aaa; padding-left: 0.6rem; }
footer { margin-top: 4rem; font-size: 0.8rem; color: #888; }
"""


def _data_root(config: dict[str, Any] | None) -> Path:
    root = (config or {}).get("data_root")
    if root:
        return Path(root)
    return Path(__file__).resolve().parents[3] / "data"


def _esc(text: str) -> str:
    return html.escape(text or "", quote=False)


def _render_post(order: int, kind, pc_ref, npc_ref, body: str, filters: FilterContext) -> str:
    kind_value = kind.value if hasattr(kind, "value") else str(kind)
    if kind_value == "pc" and pc_ref:
        label = anonymize_label(pc_ref, filters) or pc_ref
    elif kind_value == "npc" and npc_ref:
        label = anonymize_label(npc_ref, filters) or npc_ref
    else:
        label = kind_value
    paragraphs = [p.strip() for p in (body or "").split("\n\n") if p.strip()]
    if not paragraphs:
        paragraphs = [body.strip()] if body.strip() else []
    rendered_paras = "\n".join(f"<p>{_esc(p)}</p>" for p in paragraphs)
    return (
        f'<div class="post {_esc(kind_value)}" id="post-{order}">'
        f'<span class="author">{_esc(label)}:</span> {rendered_paras}'
        "</div>"
    )


class HtmlExportAdapter:
    id: ClassVar[str] = "html"
    name: ClassVar[str] = "Standalone HTML"
    extensions: ClassVar[list[str]] = ["html"]
    mime_type: ClassVar[str] = "text/html"

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        self.config: dict[str, Any] = dict(config or {})
        self._data_root = _data_root(self.config)
        self._embed_styles_default = bool(self.config.get("embed_styles", True))

    def default_options(self) -> ExportOptions:
        return ExportOptions(title="", style_preset="default")

    def option_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "embed_styles": {"type": "boolean", "default": True},
                "include_image_assets": {"type": "boolean", "default": True},
                "custom_css": {"type": "string"},
            },
            "additionalProperties": True,
        }

    async def export(
        self,
        campaign_id: str,
        selection: ExportSelection,
        options: ExportOptions,
        output_path: Path,
    ) -> ExportResult:
        snapshot = load_fs_snapshot(
            self._data_root, campaign_id, selection.branch_id.split(":")[-1]
        )
        filters = filter_context_from_dict(selection.filters)
        scenes = filter_scenes(
            snapshot,
            scene_ids=selection.scene_ids,
            include_drafts=selection.include_drafts,
        )

        extra = options.extra or {}
        embed = bool(extra.get("embed_styles", self._embed_styles_default))
        # Defuse </style> in user-supplied CSS so it can't break out of the
        # <style> block and inject HTML/JS into the shared artifact.
        css = re.sub(
            r"</\s*style",
            "<\\/style",
            str(extra.get("custom_css", _DEFAULT_CSS)),
            flags=re.IGNORECASE,
        )
        include_images = bool(extra.get("include_image_assets", True)) and selection.include_images
        appendices = set(selection.include_appendices or [])
        warnings: list[str] = []
        image_count = 0

        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        asset_dir = output_path.parent / f"{output_path.stem}_assets"
        image_links: dict[str, str] = {}
        if include_images and snapshot.images:
            asset_dir.mkdir(parents=True, exist_ok=True)
            for image in snapshot.images:
                if image.image_path and image.image_path.is_file():
                    target = asset_dir / f"{image.image_id}.png"
                    try:
                        shutil.copyfile(image.image_path, target)
                    except OSError as exc:
                        warnings.append(f"could not copy {image.image_path}: {exc!r}")
                        continue
                    image_links[image.image_id] = f"{asset_dir.name}/{image.image_id}.png"
                    image_count += 1

        title = options.title or snapshot.title

        head_styles = (
            f"<style>\n{css}\n</style>"
            if embed
            else (f'<link rel="stylesheet" href="{output_path.stem}.css">')
        )

        parts: list[str] = []
        parts.append("<!DOCTYPE html>")
        parts.append('<html lang="en">')
        parts.append("<head>")
        parts.append('<meta charset="utf-8">')
        parts.append(f"<title>{_esc(title)}</title>")
        parts.append(head_styles)
        parts.append("</head>")
        parts.append("<body>")
        parts.append("<header>")
        parts.append(f"<h1>{_esc(title)}</h1>")
        if options.subtitle:
            parts.append(f'<p class="meta">{_esc(options.subtitle)}</p>')
        if options.author:
            parts.append(f'<p class="meta">by {_esc(options.author)}</p>')
        parts.append(
            f'<p class="meta">{len(scenes)} scenes · campaign <code>{_esc(campaign_id)}</code></p>'
        )
        parts.append("</header>")

        if scenes:
            parts.append('<nav class="toc"><h2>Contents</h2><ol>')
            for record in scenes:
                anchor = f"scene-{record.scene.ordinal:04d}"
                parts.append(
                    f'<li><a href="#{anchor}">Scene {record.scene.ordinal}: '
                    f"{_esc(record.scene.title)}</a></li>"
                )
            parts.append("</ol></nav>")

        for record in scenes:
            s = record.scene
            anchor = f"scene-{s.ordinal:04d}"
            parts.append(f'<section class="scene" id="{anchor}">')
            parts.append(f"<h2>Scene {s.ordinal}: {_esc(s.title)}</h2>")
            meta_bits: list[str] = []
            if s.location_ref:
                meta_bits.append(f"Location: <code>{_esc(s.location_ref)}</code>")
            if s.in_game_start:
                meta_bits.append(f"Time: {_esc(s.in_game_start.isoformat())}")
            if s.present_pc_refs:
                anon_pcs = [anonymize_label(r, filters) or r for r in s.present_pc_refs]
                meta_bits.append("PCs: " + ", ".join(_esc(r) for r in anon_pcs))
            if meta_bits:
                parts.append('<p class="meta">' + " · ".join(meta_bits) + "</p>")
            for order, kind, pc_ref, npc_ref, body in record.posts:
                rendered = apply_filters(body, filters)
                parts.append(_render_post(order, kind, pc_ref, npc_ref, rendered, filters))
            if s.final_summary:
                parts.append(f'<p class="summary">{_esc(s.final_summary)}</p>')
            parts.append("</section>")

        if ("cast" in appendices or not appendices) and snapshot.characters:
            parts.append('<section class="appendix" id="cast">')
            parts.append("<h2>Cast</h2>")
            for card in snapshot.characters:
                parts.append(f"<h3>{_esc(card.name)}</h3>")
                desc = (card.frontmatter.get("description") or "").strip()
                if desc:
                    parts.append(f"<p>{_esc(desc)}</p>")
                if card.body.strip():
                    parts.append(f"<p>{_esc(card.body.strip())}</p>")
            parts.append("</section>")

        if include_images and image_links:
            parts.append('<section class="appendix" id="images">')
            parts.append("<h2>Image gallery</h2>")
            for image in snapshot.images:
                href = image_links.get(image.image_id)
                if not href:
                    continue
                caption = str(image.metadata.get("prompt") or image.image_id)
                src = html.escape(href, quote=True)
                alt = _esc(image.image_id)
                parts.append(
                    f'<figure><img src="{src}" alt="{alt}">'
                    f"<figcaption>{_esc(caption)}</figcaption></figure>"
                )
            parts.append("</section>")

        parts.append("<footer>")
        parts.append(f"Generated by Grimoire on {datetime.now(UTC).isoformat(timespec='seconds')}")
        parts.append("</footer>")
        parts.append("</body></html>")

        text = "\n".join(parts) + "\n"
        data = text.encode("utf-8")
        output_path.write_bytes(data)

        if not embed:
            (output_path.parent / f"{output_path.stem}.css").write_text(css, encoding="utf-8")

        return ExportResult(
            format=self.id,
            size_bytes=len(data),
            scene_count=len(scenes),
            word_count=word_count(text),
            image_count=image_count,
            file_path=str(output_path),
            warnings=warnings,
            created_at=datetime.now(UTC),
        )


__all__ = ["HtmlExportAdapter"]
