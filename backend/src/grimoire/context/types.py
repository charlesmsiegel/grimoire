"""Context building types, internal data shapes, and provider protocol."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any, Protocol

from grimoire.types.common import CampaignId, TurnId
from grimoire.types.composition import Composition
from grimoire.types.context import ContextSource
from grimoire.types.state import ContextTier


@dataclass
class TierItem:
    """One piece of structured content destined for a tier."""

    tier: ContextTier
    section: str  # 'cast' | 'location' | 'commitments' | ...
    text: str
    source: ContextSource
    priority: int = 0  # higher = packed first
    pinned: bool = False  # exempt from budget-driven eviction


@dataclass
class PinSet:
    """Active context pins / excludes for a single build.

    ``pinned_source_ids`` and ``excluded_source_ids`` reference
    ``ContextSource.source_id`` values directly. ``pinned_entities`` and
    ``excluded_entities`` are ``(kind, ref)`` tuples that match a source
    via ``(source.kind, source.owner_id)``.
    """

    pinned_source_ids: set[str] = field(default_factory=set)
    excluded_source_ids: set[str] = field(default_factory=set)
    pinned_entities: set[tuple[str, str]] = field(default_factory=set)
    excluded_entities: set[tuple[str, str]] = field(default_factory=set)

    def is_excluded(self, source: ContextSource) -> bool:
        if source.source_id and source.source_id in self.excluded_source_ids:
            return True
        return (source.kind, source.owner_id or "") in self.excluded_entities

    def is_pinned(self, source: ContextSource) -> bool:
        if source.source_id and source.source_id in self.pinned_source_ids:
            return True
        return (source.kind, source.owner_id or "") in self.pinned_entities


@dataclass
class BuiltContext:
    composition: Composition | None
    style_text: str
    content_boundaries: str
    system_meta: str
    scene_header: str
    active_pc_card: str
    active_pc_name: str
    mechanics_block: str
    commitments_block: str
    spotlight_items: list[TierItem] = field(default_factory=list)
    background_items: list[TierItem] = field(default_factory=list)
    archive_items: list[TierItem] = field(default_factory=list)
    recent_posts_text: str = ""
    voice_corrective: str = ""
    sources: list[ContextSource] = field(default_factory=list)
    extra: str | None = None
    narrator_response_mode: str = "all_at_once"
    present_npcs: list[dict] = field(default_factory=list)
    multi_call_character_name: str = ""
    multi_call_character_ref: str = ""
    pc_absent: bool = False
    scene_mode: str = ""


@dataclass(frozen=True)
class ContextBuildRequest:
    """Carries pre-parsed entities so providers avoid redundant lookups."""

    campaign_id: CampaignId
    scene: Any
    active_pc_ref: str | None
    composition: Composition | None
    player_input: str
    recent_posts: list[Any]
    turn_id: TurnId | None = None
    commitments_targeting_pcs: frozenset[str] = field(default_factory=frozenset)


class ContextProvider(Protocol):
    async def resolve(self, request: ContextBuildRequest) -> list[TierItem]: ...


def make_source_id(kind: str, owner: str | None) -> str:
    """Stable id for a ``ContextSource``.

    Deterministic across builds with identical inputs so the inspector's
    diff can pair up the same logical chunk between two previews. The hash
    is short (12 hex chars) — enough to keep collisions negligible for the
    ~hundreds of sources per turn we expect.
    """
    raw = f"{kind}:{owner or ''}"
    digest = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]
    return f"src_{digest}"
