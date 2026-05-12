"""Markdown bundle export adapter.

Produces a ``.zip`` archive containing the directory tree described in
spec 13: scenes per-file, character cards, world cards, continuity, and
optionally images. Adapters share the snapshot/filter helpers from
``grimoire.export`` so this file only owns the rendering layer.
"""

from __future__ import annotations

import io
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, ClassVar

from grimoire.export import (
    FilterContext,
    FsCampaignSnapshot,
    apply_filters,
    filter_scenes,
    load_fs_snapshot,
    word_count,
)
from grimoire.export.selection import filter_context_from_dict
from grimoire.types.export import ExportOptions, ExportResult, ExportSelection


def _data_root(config: dict[str, Any] | None) -> Path:
    root = (config or {}).get("data_root")
    if root:
        return Path(root)
    return Path(__file__).resolve().parents[3] / "data"


def _format_post(order: int, kind: str, pc_ref: str | None, npc_ref: str | None, body: str) -> str:
    if kind == "pc" and pc_ref:
        label = f"pc:{pc_ref}"
    elif kind == "npc" and npc_ref:
        label = f"npc:{npc_ref}"
    else:
        label = kind
    return f"### Post {order} — {label}\n\n{body.strip()}\n"


def _render_scene(record, filters: FilterContext) -> str:
    scene = record.scene
    lines: list[str] = [f"# Scene {scene.ordinal:04d} — {scene.title}", ""]
    if scene.location_ref:
        lines.append(f"- **Location:** `{scene.location_ref}`")
    if scene.in_game_start:
        lines.append(f"- **In-game start:** {scene.in_game_start.isoformat()}")
    if scene.present_pc_refs:
        lines.append(f"- **PCs present:** {', '.join(scene.present_pc_refs)}")
    if scene.mood:
        lines.append(f"- **Mood:** {scene.mood}")
    if scene.tags:
        lines.append(f"- **Tags:** {', '.join(scene.tags)}")
    lines.append("")
    if scene.running_summary:
        lines.append("> " + scene.running_summary.replace("\n", "\n> "))
        lines.append("")
    for order, kind, pc_ref, npc_ref, body in record.posts:
        lines.append(_format_post(order, kind, pc_ref, npc_ref, apply_filters(body, filters)))
    if scene.final_summary:
        lines.append("---")
        lines.append("")
        lines.append("**Scene summary:** " + scene.final_summary)
    return "\n".join(lines).rstrip() + "\n"


def _render_card(card) -> str:
    fm = card.frontmatter or {}
    lines: list[str] = [f"# {card.name}", ""]
    description = fm.get("description")
    if isinstance(description, str) and description.strip():
        lines.append(description.strip())
        lines.append("")
    if fm.get("tags"):
        lines.append(f"*Tags: {', '.join(str(t) for t in fm['tags'])}*")
        lines.append("")
    if card.body.strip():
        lines.append(card.body.strip())
        lines.append("")
    lines.append(f"<!-- source: {card.scope} -->\n")
    return "\n".join(lines)


def _render_index(snapshot: FsCampaignSnapshot, selected_scenes: list) -> str:
    lines = [
        f"# {snapshot.title}",
        "",
        f"- **Campaign id:** `{snapshot.campaign_id}`",
        f"- **Branch:** `{snapshot.branch_id}`",
        f"- **Scenes included:** {len(selected_scenes)}",
        f"- **Characters:** {len(snapshot.characters)}",
        f"- **Locations:** {len(snapshot.locations)}",
        f"- **Factions:** {len(snapshot.factions)}",
        f"- **Lore entries:** {len(snapshot.lore)}",
        f"- **Images:** {len(snapshot.images)}",
        "",
        "## Scenes",
        "",
    ]
    for record in selected_scenes:
        s = record.scene
        path = f"scenes/{s.ordinal:04d}-{s.slug}.md"
        lines.append(f"- [{s.ordinal:04d}-{s.slug}]({path}) — {s.title}")
    lines.append("")
    if snapshot.characters:
        lines.append("## Characters")
        lines.append("")
        for c in snapshot.characters:
            lines.append(f"- [{c.name}](characters/{c.asset_id}.md)")
        lines.append("")
    return "\n".join(lines)


def _flatten_cards(title: str, cards: list, file_hint: str) -> str:
    if not cards:
        return ""
    lines = [f"# {title}", ""]
    for card in cards:
        lines.append(f"## {card.name}")
        lines.append("")
        body = (card.frontmatter.get("description") or "").strip()
        if body:
            lines.append(body)
            lines.append("")
        if card.body.strip():
            lines.append(card.body.strip())
            lines.append("")
    return "\n".join(lines)


class MarkdownBundleAdapter:
    id: ClassVar[str] = "markdown"
    name: ClassVar[str] = "Markdown Bundle"
    extensions: ClassVar[list[str]] = ["zip"]
    mime_type: ClassVar[str] = "application/zip"

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        self.config: dict[str, Any] = dict(config or {})
        self._data_root = _data_root(self.config)
        self._include_images_default = bool(self.config.get("include_images", True))

    def default_options(self) -> ExportOptions:
        return ExportOptions(title="", style_preset="default")

    def option_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "include_image_binaries": {"type": "boolean", "default": True},
                "include_assets_metadata": {"type": "boolean", "default": True},
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

        appendices = set(selection.include_appendices or [])
        warnings: list[str] = []
        total_words = 0
        image_count = 0

        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("README.md", _render_index(snapshot, scenes))

            for record in scenes:
                rendered = _render_scene(record, filters)
                total_words += word_count(rendered)
                name = f"scenes/{record.scene.ordinal:04d}-{record.scene.slug}.md"
                zf.writestr(name, rendered)

            if "cast" in appendices or not appendices:
                for card in snapshot.characters:
                    zf.writestr(f"characters/{card.asset_id}.md", _render_card(card))
            if "setting" in appendices or not appendices:
                loc_doc = _flatten_cards("Locations", snapshot.locations, "locations")
                if loc_doc:
                    zf.writestr("setting/locations.md", loc_doc)
                lore_doc = _flatten_cards("Lore", snapshot.lore, "lore")
                if lore_doc:
                    zf.writestr("setting/lore.md", lore_doc)
                fac_doc = _flatten_cards("Factions", snapshot.factions, "factions")
                if fac_doc:
                    zf.writestr("setting/factions.md", fac_doc)
                items_doc = _flatten_cards("Items", snapshot.items, "items")
                if items_doc:
                    zf.writestr("setting/items.md", items_doc)

            include_image_binaries = bool(
                (options.extra or {}).get("include_image_binaries", self._include_images_default)
            )
            if selection.include_images and snapshot.images:
                for image in snapshot.images:
                    if include_image_binaries and image.image_path and image.image_path.is_file():
                        try:
                            zf.writestr(
                                f"images/{image.image_id}.png", image.image_path.read_bytes()
                            )
                        except OSError as exc:
                            warnings.append(f"could not read {image.image_path}: {exc!r}")
                            continue
                        image_count += 1

        data = buffer.getvalue()
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(data)

        return ExportResult(
            format=self.id,
            size_bytes=len(data),
            scene_count=len(scenes),
            word_count=total_words,
            image_count=image_count,
            file_path=str(output_path),
            warnings=warnings,
            created_at=datetime.now(UTC),
        )


__all__ = ["MarkdownBundleAdapter"]
