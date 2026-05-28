"""Plain-text transcript export adapter."""

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


class TranscriptAdapter:
    id: ClassVar[str] = "transcript"
    name: ClassVar[str] = "Plain Text Transcript"
    extensions: ClassVar[list[str]] = ["txt"]
    mime_type: ClassVar[str] = "text/plain"
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
                "show_labels": {"type": "boolean", "default": True},
                "scene_separator": {"type": "string", "default": "* * *"},
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
        snapshot = load_fs_snapshot(self._data_root, campaign_id)
        # Plain text drops mechanics chatter and OOC unless the caller said otherwise.
        raw_filters = dict(selection.filters or {})
        raw_filters.setdefault("strip_mechanics", True)
        raw_filters.setdefault("strip_ooc", True)
        raw_filters.setdefault("strip_scene_breaks", True)
        filters = filter_context_from_dict(raw_filters)

        scenes = filter_scenes(
            snapshot,
            scene_ids=selection.scene_ids,
            include_drafts=selection.include_drafts,
        )
        show_labels = bool((options.extra or {}).get("show_labels", True))
        separator = str((options.extra or {}).get("scene_separator", "* * *"))

        chunks: list[str] = []
        if options.title or snapshot.title:
            chunks.append((options.title or snapshot.title).upper())
            chunks.append("")
        if options.author:
            chunks.append(f"by {options.author}")
            chunks.append("")

        for i, record in enumerate(scenes):
            if i > 0:
                chunks.append("")
                chunks.append(separator)
                chunks.append("")
            s = record.scene
            chunks.append(f"Scene {s.ordinal}: {s.title}")
            if s.location_ref:
                chunks.append(f"({s.location_ref})")
            chunks.append("")
            for _order, kind, pc_ref, npc_ref, body in record.posts:
                filtered = apply_filters(body, filters).strip()
                if not filtered:
                    continue
                if show_labels:
                    if kind == "pc" and pc_ref:
                        speaker = anonymize_label(pc_ref, filters) or pc_ref
                        chunks.append(f"{speaker}: {filtered}")
                    elif kind == "npc" and npc_ref:
                        speaker = anonymize_label(npc_ref, filters) or npc_ref
                        chunks.append(f"{speaker}: {filtered}")
                    else:
                        chunks.append(filtered)
                else:
                    chunks.append(filtered)
                chunks.append("")

        text = "\n".join(chunks).rstrip() + "\n"
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


__all__ = ["TranscriptAdapter"]
