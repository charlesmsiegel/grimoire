"""Helpers for narrowing a :class:`FsCampaignSnapshot` down to a selection."""

from __future__ import annotations

import re
from typing import Any

from grimoire.export.data import FsCampaignSnapshot, SceneRecord
from grimoire.export.filters import FilterContext

_WORD_RE = re.compile(r"\b\w+\b", re.UNICODE)


def word_count(text: str) -> int:
    return len(_WORD_RE.findall(text or ""))


def filter_scenes(
    snapshot: FsCampaignSnapshot,
    *,
    scene_ids: list[str] | None,
    include_drafts: bool,
) -> list[SceneRecord]:
    """Return the subset of ``snapshot.scenes`` matching the selection.

    ``scene_ids=None`` means "all scenes". An empty list filters down to
    nothing — matching the conformance fixture for scene selection.
    """
    if scene_ids is None:
        scenes = list(snapshot.scenes)
    else:
        wanted = set(scene_ids)
        scenes = [s for s in snapshot.scenes if s.scene.id in wanted]
    if not include_drafts:
        scenes = [s for s in scenes if s.posts or s.scene.closed]
    return scenes


def filter_context_from_dict(raw: dict[str, Any] | None) -> FilterContext:
    """Translate the raw ``ExportSelection.filters`` mapping into a context.

    Accepts both the spec 13 v1 keys (``strip_ooc``, ``strip_mechanics``,
    ``anonymize_pcs``, ``strip_scene_breaks``) and the EPUB-pipeline keys
    (``strip_narrator_scaffolding``, ``anonymize``). Unknown keys are
    ignored.
    """
    raw = raw or {}
    anonymize: dict[str, str] = {}
    for key in ("anonymize_pcs", "anonymize"):
        value = raw.get(key)
        if isinstance(value, dict):
            anonymize.update({str(k): str(v) for k, v in value.items()})
    return FilterContext(
        strip_ooc=bool(raw.get("strip_ooc", False)),
        strip_mechanics=bool(raw.get("strip_mechanics", False)),
        strip_narrator_scaffolding=bool(
            raw.get("strip_scene_breaks", raw.get("strip_narrator_scaffolding", False))
        ),
        anonymize=anonymize,
        skip_tags=list(raw.get("skip_tags") or []),
    )


__all__ = ["filter_context_from_dict", "filter_scenes", "word_count"]
