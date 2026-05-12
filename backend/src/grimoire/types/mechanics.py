"""Mechanics API types: capabilities, rolls, validation, character creation."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from .common import CharacterRef, Json, JsonSchema, MechanicsModuleId
from .time import Duration


@dataclass
class ResourceCost:
    resource: str  # 'blood', 'spell_slot:3', 'willpower'
    amount: float
    note: str = ""


@dataclass
class Capability:
    """A mechanical thing an entity can do.

    Used by Context Builder (spotlight context), Extractor (event matching),
    Frontend (capability lists), and validation.
    """

    id: str  # e.g. 'wod.celerity.3'
    name: str
    kind: str  # 'discipline', 'spell', 'feat', 'ritual'
    description: str = ""
    cost: ResourceCost | None = None
    effect: str = ""
    metadata: Json = field(default_factory=dict)


@dataclass
class PowerDefinition:
    """The system's vocabulary entry for a named power."""

    id: str
    name: str
    kind: str
    rating: int | None = None
    description: str = ""
    cost: ResourceCost | None = None
    effect: str = ""
    metadata: Json = field(default_factory=dict)


@dataclass
class RollModifier:
    label: str
    delta: int = 0
    multiplier: float = 1.0
    metadata: Json = field(default_factory=dict)


@dataclass
class Roll:
    id: str
    kind: str  # 'dice-pool', 'attack', 'contested', ...
    pool: int
    seed: int
    actor_ref: CharacterRef | None = None
    target_ref: CharacterRef | None = None
    difficulty: int | None = None
    modifiers: list[RollModifier] = field(default_factory=list)
    metadata: Json = field(default_factory=dict)


@dataclass
class RollResult:
    roll_id: str
    dice: list[int]
    successes: int
    botched: bool = False
    outcome: str = ""
    proposed_deltas: list[Json] = field(default_factory=list)  # StateDelta at runtime
    narration_hint: str = ""


@dataclass
class ProposedRoll:
    """A roll the mechanics module suggests should be resolved pre-LLM."""

    label: str
    kind: str
    pool: int
    difficulty: int | None = None
    actor_ref: CharacterRef | None = None
    target_ref: CharacterRef | None = None
    rationale: str = ""
    high_stakes: bool = False
    modifiers: list[RollModifier] = field(default_factory=list)
    metadata: Json = field(default_factory=dict)


@dataclass
class MechanicsResult:
    """A resolved roll's payload, attached to a turn for the Context Builder."""

    roll: Roll
    result: RollResult
    summary: str = ""


@dataclass
class NarratedEvent:
    """A mechanical event the Extractor identified in prose."""

    kind: str  # 'power_use', 'damage_taken', 'wound', 'death', 'item_used', 'spell_cast'
    actor_ref: CharacterRef | None = None
    target_ref: CharacterRef | None = None
    description: str = ""
    evidence: str = ""
    metadata: Json = field(default_factory=dict)


@dataclass
class CreationStep:
    id: str
    title: str
    schema: JsonSchema
    description: str = ""
    optional: bool = False


@dataclass
class TickContext:
    """Context passed to `MechanicsModule.time_tick`."""

    campaign_id: str
    branch_id: str
    duration: Duration
    extras: Json = field(default_factory=dict)


class ApiVersion(StrEnum):
    V1 = "1"


@dataclass
class ModuleManifest:
    """A mechanics module's manifest.yaml in typed form."""

    id: MechanicsModuleId
    name: str
    version: str
    api_version: str = ApiVersion.V1.value
    author: str = ""
    homepage: str = ""
    description: str = ""
    sheet_kinds: list[str] = field(default_factory=list)
    content_kinds: list[str] = field(default_factory=list)
    capabilities: list[str] = field(default_factory=list)
    ui: Json = field(default_factory=dict)
