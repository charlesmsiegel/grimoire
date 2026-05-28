"""JSON export adapter.

Emits the campaign's structured state as a single JSON document. Useful
for backups, migrations, or feeding the data into external tools.
"""

from __future__ import annotations

import base64
import json
from dataclasses import asdict, is_dataclass
from datetime import UTC, date, datetime
from enum import Enum
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


def _to_jsonable(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, dict):
        return {str(k): _to_jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_to_jsonable(v) for v in value]
    if is_dataclass(value):
        return _to_jsonable(asdict(value))
    if hasattr(value, "model_dump"):
        try:
            return _to_jsonable(value.model_dump())
        except Exception:
            pass
    if isinstance(value, bytes):
        return base64.b64encode(value).decode("ascii")
    return repr(value)


def _scene_to_dict(record, filters: FilterContext) -> dict[str, Any]:
    s = record.scene
    return {
        "id": s.id,
        "ordinal": s.ordinal,
        "slug": s.slug,
        "title": s.title,
        "location_ref": s.location_ref,
        "in_game_start": s.in_game_start.isoformat() if s.in_game_start else None,
        "in_game_end": s.in_game_end.isoformat() if s.in_game_end else None,
        "present_pc_refs": list(s.present_pc_refs),
        "present_character_refs": list(s.present_character_refs),
        "pov_character_ref": s.pov_character_ref,
        "mood": s.mood,
        "tags": list(s.tags),
        "closed": s.closed,
        "running_summary": s.running_summary,
        "final_summary": s.final_summary,
        "key_beats": list(s.key_beats),
        "threads_introduced": [t.text for t in s.threads_introduced],
        "threads_paid_off": [t.text for t in s.threads_paid_off],
        "posts": [
            {
                "order": order,
                "author_kind": kind.value if hasattr(kind, "value") else str(kind),
                "author_pc_ref": pc_ref,
                "author_npc_ref": npc_ref,
                "body": apply_filters(body, filters),
            }
            for order, kind, pc_ref, npc_ref, body in record.posts
        ],
    }


def _card_to_dict(card) -> dict[str, Any]:
    return {
        "kind": card.kind,
        "id": card.asset_id,
        "name": card.name,
        "scope": card.scope,
        "frontmatter": dict(card.frontmatter),
        "body": card.body,
    }


def _image_to_dict(image) -> dict[str, Any]:
    return {
        "id": image.image_id,
        "metadata": dict(image.metadata),
        "image_path": str(image.image_path) if image.image_path else None,
    }


class JsonExportAdapter:
    id: ClassVar[str] = "json"
    name: ClassVar[str] = "JSON Dump"
    extensions: ClassVar[list[str]] = ["json"]
    mime_type: ClassVar[str] = "application/json"
    capabilities: ClassVar[ExportCapabilities] = ExportCapabilities(
        supports_images=False,
        supports_appendices=True,
        supports_filters=True,
        supported_style_presets=[],
    )

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        self.config: dict[str, Any] = dict(config or {})
        self._data_root = _data_root(self.config)
        self._pretty_default = bool(self.config.get("pretty_print", True))
        self._include_embeddings_default = bool(self.config.get("include_embeddings", False))

    def default_options(self) -> ExportOptions:
        return ExportOptions(title="", style_preset="default")

    def option_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "pretty_print": {"type": "boolean", "default": True},
                "include_embeddings": {"type": "boolean", "default": False},
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
        snapshot: FsCampaignSnapshot = load_fs_snapshot(self._data_root, campaign_id)
        filters = filter_context_from_dict(selection.filters)
        scenes = filter_scenes(
            snapshot,
            scene_ids=selection.scene_ids,
            include_drafts=selection.include_drafts,
        )

        scene_dicts = [_scene_to_dict(r, filters) for r in scenes]
        total_words = sum(word_count(p["body"]) for s in scene_dicts for p in s["posts"])

        appendices = set(selection.include_appendices or [])
        include_all = not appendices

        payload: dict[str, Any] = {
            "schema_version": "1",
            "campaign": {
                "id": snapshot.campaign_id,
                "title": snapshot.title,
                "metadata": _to_jsonable(snapshot.campaign_yaml),
            },
            "options": {
                "title": options.title,
                "subtitle": options.subtitle,
                "author": options.author,
                "style_preset": options.style_preset,
            },
            "scenes": scene_dicts,
        }
        if include_all or "cast" in appendices:
            payload["characters"] = [_card_to_dict(c) for c in snapshot.characters]
        if include_all or "world" in appendices:
            payload["locations"] = [_card_to_dict(c) for c in snapshot.locations]
            payload["lore"] = [_card_to_dict(c) for c in snapshot.lore]
            payload["factions"] = [_card_to_dict(c) for c in snapshot.factions]
            payload["items"] = [_card_to_dict(c) for c in snapshot.items]
            payload["greetings"] = [_card_to_dict(c) for c in snapshot.greetings]
        if selection.include_images and (include_all or "image_gallery" in appendices):
            payload["images"] = [_image_to_dict(i) for i in snapshot.images]

        pretty = bool((options.extra or {}).get("pretty_print", self._pretty_default))
        if pretty:
            text = json.dumps(payload, indent=2, ensure_ascii=False, default=_to_jsonable)
        else:
            text = json.dumps(payload, ensure_ascii=False, default=_to_jsonable)
        data = text.encode("utf-8")
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(data)

        return ExportResult(
            format=self.id,
            size_bytes=len(data),
            scene_count=len(scenes),
            word_count=total_words,
            image_count=len(payload.get("images", [])),
            file_path=str(output_path),
            warnings=[],
            created_at=datetime.now(UTC),
        )


__all__ = ["JsonExportAdapter"]
