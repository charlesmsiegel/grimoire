"""World module types: locations, items, factions, lore, weather, calendar.

Spec 09. Wraps the typed view of the entity kinds that live inside a world
directory plus the procedural fixtures the World module computes on top of
them (weather, season, holiday). Character behaviors live in
``types/characters.py``; sheets live behind the mechanics module API.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .common import (
    BranchId,
    CampaignId,
    CharacterRef,
    FactionRef,
    InGameTime,
    Json,
    LocationRef,
)
from .extras import ExtraValue, validate_extras_dict


def _validate_extras_before(v: Any) -> Any:
    return validate_extras_dict(v)


class LocationKind(StrEnum):
    CITY = "city"
    BUILDING = "building"
    ROOM = "room"
    REGION = "region"
    OUTDOOR = "outdoor"
    OTHER = "other"


class Coords(BaseModel):
    model_config = ConfigDict(frozen=True)

    x: float
    y: float


class LocationConnection(BaseModel):
    to: str  # asset_id of the connected location (same world)
    via: str = ""  # 'street', 'door', 'path', 'gate', ...
    duration_min: int = 0
    notes: str = ""


class Location(BaseModel):
    world_id: str
    id: str
    name: str
    parent_id: str | None = None
    kind: LocationKind = LocationKind.OTHER
    aliases: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    climate_zone: str | None = None
    indoor: bool = False
    coordinates: Coords | None = None
    permanent_features: list[str] = Field(default_factory=list)
    connections: list[LocationConnection] = Field(default_factory=list)
    typical_occupants: list[str] = Field(default_factory=list)
    description: str = ""
    body: str = ""
    extras: dict[str, ExtraValue] = Field(default_factory=dict)

    _check_extras = field_validator("extras", mode="before")(_validate_extras_before)


class Item(BaseModel):
    world_id: str
    id: str
    name: str
    aliases: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    provenance: str | None = None
    current_holder: str | None = None
    description: str = ""
    body: str = ""
    extras: dict[str, ExtraValue] = Field(default_factory=dict)

    _check_extras = field_validator("extras", mode="before")(_validate_extras_before)


class Faction(BaseModel):
    world_id: str
    id: str
    name: str
    kind: str = ""
    base_location: str | None = None
    leaders: list[str] = Field(default_factory=list)
    members: list[str] = Field(default_factory=list)
    allies: list[str] = Field(default_factory=list)
    rivals: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    description: str = ""
    body: str = ""
    extras: dict[str, ExtraValue] = Field(default_factory=dict)

    _check_extras = field_validator("extras", mode="before")(_validate_extras_before)


class SecrecyLevel(StrEnum):
    PUBLIC = "public"
    COMMON_KNOWLEDGE = "common-knowledge"
    COMMON_KNOWLEDGE_AMONG = "common-knowledge-among-kindred"  # spec example
    RESTRICTED = "restricted"
    SECRET = "secret"


class LorePosition(StrEnum):
    """Where Context Builder injects a triggered lore entry.

    See ``docs/superpowers/specs/2026-05-19-card-imports-design.md`` §4.
    """

    BEFORE_CAST = "before_cast"
    AFTER_CAST = "after_cast"
    AT_DEPTH = "at_depth"
    ARCHIVE = "archive"


class SelectiveLogic(StrEnum):
    """How ``LoreEntry.secondary_keys`` combine with the primary match."""

    AND_ANY = "and_any"
    AND_ALL = "and_all"
    NOT_ANY = "not_any"
    NOT_ALL = "not_all"


class ImportSource(BaseModel):
    """Provenance for entries imported from external character cards."""

    kind: str
    card_asset_id: str
    source_index: int


class LoreEntry(BaseModel):
    world_id: str
    id: str
    title: str
    body: str = ""
    tags: list[str] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list)
    related_locations: list[str] = Field(default_factory=list)
    related_factions: list[str] = Field(default_factory=list)
    related_characters: list[str] = Field(default_factory=list)
    secrecy: str = SecrecyLevel.PUBLIC.value

    # Extended fields (spec 2026-05-19-card-imports §3); all optional with
    # backwards-compatible defaults so hand-written lore files parse unchanged.
    secondary_keys: list[str] = Field(default_factory=list)
    selective_logic: SelectiveLogic = SelectiveLogic.AND_ANY
    constant: bool = False
    enabled: bool = True
    case_sensitive: bool = False
    match_whole_words: bool = False
    priority: int = 100
    probability: int = 100
    position: LorePosition = LorePosition.AFTER_CAST
    at_depth: int | None = None
    scan_depth: int | None = None
    comment: str = ""
    import_source: ImportSource | None = None


class FactionGoal(BaseModel):
    id: str
    description: str
    progress: float = 0.0  # 0.0 -- 1.0
    deadline: datetime | None = None


class FactionStateData(BaseModel):
    """Decoded campaign-scoped faction state (the ``state`` blob in SQLite)."""

    faction_ref: FactionRef
    campaign_id: CampaignId
    branch_id: BranchId
    goals: list[FactionGoal] = Field(default_factory=list)
    resources: Json = Field(default_factory=dict)
    current_focus: str = ""
    public_perception: str = ""
    secrets: list[str] = Field(default_factory=list)
    updated_at_turn: str | None = None


class WeatherKind(StrEnum):
    CLEAR = "clear"
    OVERCAST = "overcast"
    RAIN = "rain"
    STORM = "storm"
    SNOW = "snow"
    FOG = "fog"
    WIND = "wind"
    HEAT = "heat"
    COLD = "cold"


class Weather(BaseModel):
    kind: WeatherKind = WeatherKind.CLEAR
    summary: str = ""  # short prose ("light drizzle over a low haze")
    temperature_c: float | None = None
    humidity: float | None = None  # 0..1
    wind_kph: float | None = None
    palette: str = ""  # mood / colour cue
    source: str = "procedural"  # 'procedural' | 'override' | 'fixed'


class Month(BaseModel):
    name: str
    days: int


class Season(BaseModel):
    name: str
    start_month: int = 1  # 1-indexed
    start_day: int = 1
    palette: str = ""
    weather_bias: dict[str, float] = Field(default_factory=dict)


class Holiday(BaseModel):
    name: str
    month: int  # 1-indexed
    day: int
    description: str = ""
    tags: list[str] = Field(default_factory=list)


class WorldCalendar(BaseModel):
    world_id: str
    epoch: datetime | None = None
    months: list[Month] = Field(default_factory=list)
    days_per_week: int = 7
    week_day_names: list[str] = Field(default_factory=list)
    seasons: list[Season] = Field(default_factory=list)
    holidays: list[Holiday] = Field(default_factory=list)


class LocationStateData(BaseModel):
    """Decoded campaign-scoped location state."""

    location_ref: LocationRef
    campaign_id: CampaignId
    branch_id: BranchId
    weather: Weather | None = None
    time_of_day: str = ""
    occupants: list[CharacterRef] = Field(default_factory=list)
    condition: str = ""
    transient_features: list[str] = Field(default_factory=list)
    updated_at_turn: str | None = None


def in_game_time(value: datetime) -> InGameTime:
    """Convenience wrapper used by World helpers."""
    return InGameTime(moment=value)
