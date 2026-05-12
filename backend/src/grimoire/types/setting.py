"""Setting module types: locations, items, factions, lore, weather, calendar.

Spec 09. Wraps the typed view of the entity kinds that live inside a setting
directory plus the procedural fixtures the Setting module computes on top of
them (weather, season, holiday). Character behaviors live in
``types/characters.py``; sheets live behind the mechanics module API.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from .common import (
    BranchId,
    CampaignId,
    CharacterRef,
    FactionRef,
    InGameTime,
    Json,
    LocationRef,
)


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
    to: str  # asset_id of the connected location (same setting)
    via: str = ""  # 'street', 'door', 'path', 'gate', ...
    duration_min: int = 0
    notes: str = ""


class Location(BaseModel):
    setting_id: str
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


class Item(BaseModel):
    setting_id: str
    id: str
    name: str
    aliases: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    provenance: str | None = None
    current_holder: str | None = None
    description: str = ""
    body: str = ""


class Faction(BaseModel):
    setting_id: str
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


class SecrecyLevel(StrEnum):
    PUBLIC = "public"
    COMMON_KNOWLEDGE = "common-knowledge"
    COMMON_KNOWLEDGE_AMONG = "common-knowledge-among-kindred"  # spec example
    RESTRICTED = "restricted"
    SECRET = "secret"


class LoreEntry(BaseModel):
    setting_id: str
    id: str
    title: str
    body: str = ""
    tags: list[str] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list)
    related_locations: list[str] = Field(default_factory=list)
    related_factions: list[str] = Field(default_factory=list)
    related_characters: list[str] = Field(default_factory=list)
    secrecy: str = SecrecyLevel.PUBLIC.value


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


class SettingCalendar(BaseModel):
    setting_id: str
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
    """Convenience wrapper used by Setting helpers."""
    return InGameTime(moment=value)
