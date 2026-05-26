"""Import scene files from arbitrary disk locations."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from grimoire.files import load_yaml
from grimoire.scenes.storage import PostTuple, parse_body
from grimoire.scenes.types import AuthorKind

logger = logging.getLogger(__name__)


@dataclass
class ImportParseResult:
    post_count: int
    posts: list[PostTuple]
    detected_pc_refs: list[str]
    detected_npc_refs: list[str]
    sidecar_metadata: dict[str, Any] | None = None


def parse_import_source(md_path: Path) -> ImportParseResult:
    """Parse a grimoire-format scene .md and optional .yaml sidecar."""
    text = md_path.read_text(encoding="utf-8")
    posts = parse_body(text, scene_id="__import_preview__")

    pc_refs: list[str] = []
    npc_refs: list[str] = []
    for _order, kind, pc_ref, npc_ref, _body in posts:
        if kind == AuthorKind.PC and pc_ref and pc_ref not in pc_refs:
            pc_refs.append(pc_ref)
        if kind == AuthorKind.NPC and npc_ref and npc_ref not in npc_refs:
            npc_refs.append(npc_ref)

    sidecar_meta: dict[str, Any] | None = None
    yaml_path = md_path.with_suffix(".yaml")
    if yaml_path.is_file():
        raw = load_yaml(yaml_path)
        if isinstance(raw, dict):
            sidecar_meta = {
                k: raw[k]
                for k in (
                    "title", "location_ref", "in_game_start", "in_game_end",
                    "mood", "tags", "present_character_refs", "present_pc_refs",
                )
                if k in raw
            }

    return ImportParseResult(
        post_count=len(posts),
        posts=posts,
        detected_pc_refs=pc_refs,
        detected_npc_refs=npc_refs,
        sidecar_metadata=sidecar_meta,
    )
