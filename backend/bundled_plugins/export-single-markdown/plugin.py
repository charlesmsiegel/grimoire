"""Single-file Markdown export adapter."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any, ClassVar

from grimoire.export import (
    anonymize_label,
    apply_filters,
    filter_scenes,
    load_fs_snapshot,
    word_count,
)
from grimoire.export.selection import filter_context_from_dict
from grimoire.types.export import (
    ExportCapabilities,
    ExportOptions,
    ExportResult,
    ExportSelection,
)


def _data_root(config: dict[str, Any] | None) -> Path:
    root = (config or {}).get("data_root")
    if root:
        return Path(root)
    return Path(__file__).resolve().parents[3] / "data"


class SingleMarkdownAdapter:
    id: ClassVar[str] = "single_markdown"
    name: ClassVar[str] = "Single Markdown File"
    extensions: ClassVar[list[str]] = ["md"]
    mime_type: ClassVar[str] = "text/markdown"
    capabilities: ClassVar[ExportCapabilities] = ExportCapabilities(
        supports_images=False,
        supports_appendices=False,
        supports_filters=True,
        supported_style_presets=[],
    )

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        self.config: dict[str, Any] = dict(config or {})
        self._data_root = _data_root(self.config)

    def default_options(self) -> ExportOptions:
        return ExportOptions(title="", style_preset="default")

    def option_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "show_post_labels": {"type": "boolean", "default": True},
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
        show_labels = bool((options.extra or {}).get("show_post_labels", True))
        appendices = set(selection.include_appendices or [])

        lines: list[str] = []
        title = options.title or snapshot.title
        lines.append(f"# {title}")
        if options.subtitle:
            lines.append(f"## {options.subtitle}")
        if options.author:
            lines.append(f"*by {options.author}*")
        lines.append("")

        if scenes:
            lines.append("## Table of contents")
            lines.append("")
            for record in scenes:
                s = record.scene
                anchor = f"scene-{s.ordinal:04d}-{s.slug}"
                lines.append(f"- [Scene {s.ordinal}: {s.title}](#{anchor})")
            lines.append("")

        for record in scenes:
            s = record.scene
            anchor = f"scene-{s.ordinal:04d}-{s.slug}"
            lines.append(f'<a id="{anchor}"></a>')
            lines.append(f"## Scene {s.ordinal}: {s.title}")
            lines.append("")
            if s.location_ref:
                lines.append(f"*Location: `{s.location_ref}`*  ")
            if s.in_game_start:
                lines.append(f"*Time: {s.in_game_start.isoformat()}*")
            lines.append("")
            for _order, kind, pc_ref, npc_ref, body in record.posts:
                filtered = apply_filters(body, filters)
                if show_labels:
                    if kind == "pc" and pc_ref:
                        label = f"**{anonymize_label(pc_ref, filters) or pc_ref}**"
                    elif kind == "npc" and npc_ref:
                        label = f"*{anonymize_label(npc_ref, filters) or npc_ref}*"
                    else:
                        label = f"*{kind}*"
                    lines.append(f"{label}: {filtered}")
                else:
                    lines.append(filtered)
                lines.append("")
            if s.final_summary:
                lines.append(f"> {s.final_summary}")
                lines.append("")

        if ("cast" in appendices or not appendices) and snapshot.characters:
            lines.append("## Cast")
            lines.append("")
            for c in snapshot.characters:
                lines.append(f"### {c.name}")
                lines.append("")
                desc = (c.frontmatter.get("description") or "").strip()
                if desc:
                    lines.append(desc)
                    lines.append("")
                if c.body.strip():
                    lines.append(c.body.strip())
                    lines.append("")

        text = "\n".join(lines).rstrip() + "\n"
        data = text.encode("utf-8")
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(data)

        return ExportResult(
            format=self.id,
            size_bytes=len(data),
            scene_count=len(scenes),
            word_count=word_count(text),
            image_count=0,
            file_path=str(output_path),
            warnings=[],
            created_at=datetime.now(UTC),
        )


__all__ = ["SingleMarkdownAdapter"]
