"""Shared primitives referenced across modules."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

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
    SETTING = "setting"
    STYLE_GUIDE = "style_guide"
    IMAGE_PRESET = "image_preset"


@dataclass(frozen=True)
class EntityRef:
    """Reference to an entity that may be resolved through the campaign cascade.

    Examples:
        library:settings/wod-london/characters/alistair-hyde-smythe
        campaign:emergent/characters/the-bartender
    """

    scope: Scope
    kind: EntityKind
    setting_id: str | None
    asset_id: str
    raw: str

    @classmethod
    def parse(cls, raw: str) -> EntityRef:
        """Best-effort parse of the string form. Format is not strictly enforced."""
        scope_part, _, path = raw.partition(":")
        scope = Scope(scope_part) if scope_part in Scope._value2member_map_ else Scope.LIBRARY
        parts = path.split("/") if path else []
        setting_id: str | None = None
        kind = EntityKind.CHARACTER
        asset_id = parts[-1] if parts else raw
        if len(parts) >= 4 and parts[0] == "settings":
            setting_id = parts[1]
            try:
                kind_part = parts[2].rstrip("s") if parts[2].endswith("s") else parts[2]
                kind = EntityKind(kind_part)
            except ValueError:
                kind = EntityKind.CHARACTER
        return cls(scope=scope, kind=kind, setting_id=setting_id, asset_id=asset_id, raw=raw)


CharacterRef = str
SceneRef = str
LocationRef = str
FactionRef = str
ItemRef = str
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


@dataclass
class HealthStatus:
    level: HealthLevel
    target_id: str
    message: str = ""
    checked_at: str | None = None  # ISO 8601 timestamp
    details: Json = field(default_factory=dict)


@dataclass
class ValidationResult:
    valid: bool
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    proposed_deltas: list[Any] = field(default_factory=list)  # list[StateDelta] at runtime
