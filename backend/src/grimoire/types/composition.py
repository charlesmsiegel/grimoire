"""Campaign composition: world refs, library entities, resolved entities."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field

from .common import CampaignId, EntityKind, Json, Scope


class WorldRef(BaseModel):
    world_id: str
    priority: int  # 1 = highest
    # None / missing means "include every kind"; an explicit list (even empty)
    # is treated literally — `[]` means include nothing from this world.
    include: list[str] | None = None
    # ['characters', 'items', 'locations', 'lore', 'factions', 'greetings']
    bound_at_version: int = 0
    track_latest: bool = False


class Composition(BaseModel):
    worlds: list[WorldRef] = Field(default_factory=list)
    mechanics: str | None = None  # mechanics module id, or None
    style_guide_id: str | None = None
    image_preset_id: str | None = None
    inline_style_guide: str | None = None
    content_boundaries: str | None = None
    # Calendars attached to this campaign. When empty, the campaign falls
    # back to the worlds' calendars. The display calendar is the one shown
    # to the user for scene tracking; the others are still tracked in the
    # backend so any date can be rendered in any attached system.
    calendar_ids: list[str] = Field(default_factory=list)
    holiday_set_ids: list[str] = Field(default_factory=list)
    display_calendar_id: str | None = None


class WorldMeta(BaseModel):
    id: str
    name: str
    description: str = ""
    tags: list[str] = Field(default_factory=list)
    pc_role_tags: list[str] = Field(default_factory=list)
    genre: str = ""
    # Legacy inline calendar (kept as fallback). New worlds should use
    # `calendar_ids` to attach first-class Calendar entities instead.
    calendar: Json = Field(default_factory=dict)
    # Calendars attached to this world. When non-empty, the world supports
    # multiple concurrent calendars reconciled through JDN. The display
    # calendar (display_calendar_id) is the one rendered in scenes by
    # default; others can be queried via the conversion endpoint.
    calendar_ids: list[str] = Field(default_factory=list)
    holiday_set_ids: list[str] = Field(default_factory=list)
    display_calendar_id: str | None = None
    atmosphere: Json = Field(default_factory=dict)
    defaults: Json = Field(default_factory=dict)
    version: int = 0


class LibraryEntity(BaseModel):
    """Raw library entity as read from a markdown + YAML file."""

    id: str  # composite path, e.g. worlds/wod-london/characters/alistair
    world_id: str | None
    kind: EntityKind
    asset_id: str
    name: str
    path: str
    frontmatter: Json
    body: str
    body_compressed: str | None = None
    tags: list[str] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list)
    file_mtime: datetime | None = None
    content_hash: str = ""
    indexed_at: datetime | None = None
    version: int = 0


class Greeting(BaseModel):
    id: str
    world_id: str
    name: str
    starting_location: str | None
    starting_time: str | None  # ISO 8601 in the world's calendar
    present_characters: list[str] = Field(default_factory=list)
    pov_character: str | None = None
    mood: str = ""
    body: str = ""
    tags: list[str] = Field(default_factory=list)
    role_tags: list[str] = Field(default_factory=list)


class ResolutionLayer(StrEnum):
    EMERGENT = "emergent"
    OVERRIDE = "override"
    LIBRARY_SNAPSHOT = "library_snapshot"
    LIBRARY_LIVE = "library_live"


class ResolutionSource(BaseModel):
    """Where one slice of a resolved entity came from."""

    layer: ResolutionLayer
    scope: Scope
    library_id: str | None = None
    world_id: str | None = None
    version: int | None = None
    override_applied: bool = False


class ResolvedEntity(BaseModel):
    """An entity after the cascade has been applied.

    Domain modules (Library, World, Characters) emit these; consumers
    (Context Builder, UI) read them.
    """

    kind: EntityKind
    asset_id: str
    world_id: str | None
    name: str
    frontmatter: Json
    body: str
    source_chain: list[ResolutionSource] = Field(default_factory=list)
    overrides_applied: list[str] = Field(default_factory=list)
    extras: Json = Field(default_factory=dict)  # capabilities, current_state, etc.


class ResolvedLocation(BaseModel):
    asset_id: str
    world_id: str | None
    name: str
    frontmatter: Json
    body: str
    parent_id: str | None = None
    connections: list[Json] = Field(default_factory=list)
    source_chain: list[ResolutionSource] = Field(default_factory=list)
    overrides_applied: list[str] = Field(default_factory=list)


class CampaignRef(BaseModel):
    id: CampaignId
    name: str


class UpgradeReport(BaseModel):
    campaign_id: CampaignId
    world_id: str
    from_version: int
    to_version: int
    changed_entities: list[str] = Field(default_factory=list)
    added_entities: list[str] = Field(default_factory=list)
    removed_entities: list[str] = Field(default_factory=list)
    diff: Any | None = None


class UpgradeEntityChange(BaseModel):
    """Per-entity diff payload for an :class:`UpgradePreview`.

    ``before`` is the pinned-snapshot frontmatter/body the campaign sees
    today; ``after`` is what the live library row would show post-upgrade.
    Either side may be missing — entities added or removed by the upgrade
    surface here as well.
    """

    library_id: str
    before_version: int | None = None
    after_version: int | None = None
    before_frontmatter: dict[str, Any] | None = None
    after_frontmatter: dict[str, Any] | None = None
    before_body: str | None = None
    after_body: str | None = None


class UpgradePreview(BaseModel):
    """Dry-run output for :meth:`LibraryService.preview_upgrade_world_ref`.

    Pairs the existing :class:`UpgradeReport` shape (version numbers +
    diff map) with the per-entity content the frontend needs to render an
    inline diff before the user commits the upgrade.
    """

    campaign_id: CampaignId
    world_id: str
    from_version: int
    to_version: int
    changed_entities: list[str] = Field(default_factory=list)
    added_entities: list[str] = Field(default_factory=list)
    removed_entities: list[str] = Field(default_factory=list)
    entries: list[UpgradeEntityChange] = Field(default_factory=list)
