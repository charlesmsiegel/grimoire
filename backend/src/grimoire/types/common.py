"""Shared primitives referenced across modules.

These are the foundational types that other modules build on. Keeping
`InGameTime` and `Duration` here (rather than in `time.py`) avoids a circular
dependency: `time.TimeAdvanceResult` references `Continuity.Commitment` and
`State.StateDelta`, and both of those need to talk about in-game time.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

JsonSchema = dict[str, Any]
Json = dict[str, Any]


class Scope(StrEnum):
    LIBRARY = "library"
    CAMPAIGN_LOCAL = "campaign-local"
    CAMPAIGN_SQLITE = "campaign-sqlite"
    CAMPAIGN_FILE = "campaign-file"


class EntityKind(StrEnum):
    CHARACTER = "character"
    ITEM = "item"
    LOCATION = "location"
    LORE = "lore"
    FACTION = "faction"
    GREETING = "greeting"
    MONSTER = "monster"
    World = "world"
    STYLE_GUIDE = "style_guide"
    IMAGE_PRESET = "image_preset"
    CALENDAR = "calendar"
    HOLIDAY_SET = "holiday_set"


class EntityRef(BaseModel):
    """Reference to an entity that may be resolved through the campaign cascade.

    Examples:
        library:worlds/wod-london/characters/alistair-hyde-smythe
        campaign:emergent/characters/the-bartender
    """

    model_config = ConfigDict(frozen=True)

    scope: Scope
    kind: EntityKind
    world_id: str | None
    asset_id: str
    raw: str

    @classmethod
    def parse(cls, raw: str) -> EntityRef:
        """Best-effort parse of the string form. Format is not strictly enforced."""
        scope_part, _, path = raw.partition(":")
        scope = Scope(scope_part) if scope_part in Scope._value2member_map_ else Scope.LIBRARY
        parts = path.split("/") if path else []
        world_id: str | None = None
        kind = EntityKind.CHARACTER
        asset_id = parts[-1] if parts else raw
        if len(parts) >= 4 and parts[0] == "worlds":
            world_id = parts[1]
            try:
                kind_part = parts[2].rstrip("s") if parts[2].endswith("s") else parts[2]
                kind = EntityKind(kind_part)
            except ValueError:
                kind = EntityKind.CHARACTER
        return cls(scope=scope, kind=kind, world_id=world_id, asset_id=asset_id, raw=raw)


CharacterRef = str
SceneRef = str
LocationRef = str
FactionRef = str
ItemRef = str
MonsterRef = str
CampaignId = str
BranchId = str
TurnId = str
PostId = str
SceneId = str
FactId = str
CommitmentId = str
EventId = str
SubscriptionId = str
GenJobId = str
PluginId = str
MechanicsModuleId = str


class HealthLevel(StrEnum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNHEALTHY = "unhealthy"
    UNCONFIGURED = "unconfigured"


class HealthStatus(BaseModel):
    level: HealthLevel
    target_id: str
    message: str = ""
    checked_at: str | None = None  # ISO 8601 timestamp
    details: Json = Field(default_factory=dict)


class ValidationResult(BaseModel):
    """Result of validating sheets, events, plugin config, etc.

    `proposed_deltas` is typed `list[Any]` to avoid a circular import with
    `state.StateDelta`; consumers narrow it at the call site.
    """

    valid: bool
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    proposed_deltas: list[Any] = Field(default_factory=list)


class InGameTime(BaseModel):
    """A point in a campaign's in-game calendar.

    Wraps a `datetime`. Calendars may be Earth-Gregorian or world-defined;
    world-specific calendar metadata lives on the `WorldMeta`.
    """

    model_config = ConfigDict(frozen=True)

    moment: datetime
    calendar_id: str | None = None  # world calendar id; None = Gregorian


class Duration(BaseModel):
    """A span of in-game time.

    Stored as an ISO 8601 duration string plus a resolved `timedelta` so the
    callable surface stays simple. Months/years cannot be exactly represented
    by `timedelta`; the canonical form is `iso8601`.
    """

    model_config = ConfigDict(frozen=True)

    iso8601: str
    delta: timedelta = Field(default_factory=timedelta)
