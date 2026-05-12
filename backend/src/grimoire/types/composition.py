"""Campaign composition: setting refs, library entities, resolved entities."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any

from .common import CampaignId, EntityKind, Json, Scope


@dataclass
class SettingRef:
    setting_id: str
    priority: int  # 1 = highest
    include: list[str] = field(default_factory=list)
    # ['characters', 'items', 'locations', 'lore', 'factions', 'greetings']
    bound_at_version: int = 0
    track_latest: bool = False


@dataclass
class Composition:
    settings: list[SettingRef] = field(default_factory=list)
    mechanics: str | None = None  # mechanics module id, or None
    style_guide_id: str | None = None
    image_preset_id: str | None = None
    inline_style_guide: str | None = None
    content_boundaries: str | None = None


@dataclass
class SettingMeta:
    id: str
    name: str
    description: str = ""
    tags: list[str] = field(default_factory=list)
    genre: str = ""
    calendar: Json = field(default_factory=dict)
    atmosphere: Json = field(default_factory=dict)
    defaults: Json = field(default_factory=dict)
    version: int = 0


@dataclass
class LibraryEntity:
    """Raw library entity as read from a markdown + YAML file."""

    id: str  # composite path, e.g. settings/wod-london/characters/alistair
    setting_id: str | None
    kind: EntityKind
    asset_id: str
    name: str
    path: str
    frontmatter: Json
    body: str
    body_compressed: str | None = None
    tags: list[str] = field(default_factory=list)
    keywords: list[str] = field(default_factory=list)
    file_mtime: datetime | None = None
    content_hash: str = ""
    indexed_at: datetime | None = None
    version: int = 0


@dataclass
class Greeting:
    id: str
    setting_id: str
    name: str
    starting_location: str | None
    starting_time: str | None  # ISO 8601 in the setting's calendar
    present_characters: list[str] = field(default_factory=list)
    pov_character: str | None = None
    mood: str = ""
    body: str = ""
    tags: list[str] = field(default_factory=list)


class ResolutionLayer(StrEnum):
    EMERGENT = "emergent"
    OVERRIDE = "override"
    LIBRARY_SNAPSHOT = "library_snapshot"
    LIBRARY_LIVE = "library_live"


@dataclass
class ResolutionSource:
    """Where one slice of a resolved entity came from."""

    layer: ResolutionLayer
    scope: Scope
    library_id: str | None = None
    setting_id: str | None = None
    version: int | None = None
    override_applied: bool = False


@dataclass
class ResolvedEntity:
    """An entity after the cascade has been applied.

    Domain modules (Library, Setting, Characters) emit these; consumers
    (Context Builder, UI) read them.
    """

    kind: EntityKind
    asset_id: str
    setting_id: str | None
    name: str
    frontmatter: Json
    body: str
    source_chain: list[ResolutionSource] = field(default_factory=list)
    overrides_applied: list[str] = field(default_factory=list)
    extras: Json = field(default_factory=dict)  # capabilities, current_state, etc.


@dataclass
class ResolvedLocation:
    asset_id: str
    setting_id: str | None
    name: str
    frontmatter: Json
    body: str
    parent_id: str | None = None
    connections: list[Json] = field(default_factory=list)
    source_chain: list[ResolutionSource] = field(default_factory=list)
    overrides_applied: list[str] = field(default_factory=list)


@dataclass
class CampaignRef:
    id: CampaignId
    name: str


@dataclass
class UpgradeReport:
    campaign_id: CampaignId
    setting_id: str
    from_version: int
    to_version: int
    changed_entities: list[str] = field(default_factory=list)
    added_entities: list[str] = field(default_factory=list)
    removed_entities: list[str] = field(default_factory=list)
    diff: Any | None = None
