"""Mechanics API types: capabilities, rolls, validation, character creation."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field

from .common import CharacterRef, Duration, Json, JsonSchema, MechanicsModuleId
from .hud import HudWidget


class ResourceCost(BaseModel):
    resource: str  # 'blood', 'spell_slot:3', 'willpower'
    amount: float
    note: str = ""


class Capability(BaseModel):
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
    metadata: Json = Field(default_factory=dict)


class PowerDefinition(BaseModel):
    """The system's vocabulary entry for a named power."""

    id: str
    name: str
    kind: str
    rating: int | None = None
    description: str = ""
    cost: ResourceCost | None = None
    effect: str = ""
    metadata: Json = Field(default_factory=dict)


class RollModifier(BaseModel):
    label: str
    delta: int = 0
    multiplier: float = 1.0
    metadata: Json = Field(default_factory=dict)


class Roll(BaseModel):
    id: str
    kind: str  # 'dice-pool', 'attack', 'contested', ...
    pool: int
    seed: int
    actor_ref: CharacterRef | None = None
    target_ref: CharacterRef | None = None
    difficulty: int | None = None
    modifiers: list[RollModifier] = Field(default_factory=list)
    metadata: Json = Field(default_factory=dict)


class RollResult(BaseModel):
    roll_id: str
    dice: list[int]
    successes: int
    botched: bool = False
    outcome: str = ""
    proposed_deltas: list[Json] = Field(default_factory=list)  # StateDelta at runtime
    narration_hint: str = ""


class ProposedRoll(BaseModel):
    """A roll the mechanics module suggests should be resolved pre-LLM."""

    label: str
    kind: str
    pool: int
    difficulty: int | None = None
    actor_ref: CharacterRef | None = None
    target_ref: CharacterRef | None = None
    rationale: str = ""
    high_stakes: bool = False
    modifiers: list[RollModifier] = Field(default_factory=list)
    metadata: Json = Field(default_factory=dict)


class MechanicsResult(BaseModel):
    """A resolved roll's payload, attached to a turn for the Context Builder."""

    roll: Roll
    result: RollResult
    summary: str = ""


class NarratedEvent(BaseModel):
    """A mechanical event the Extractor identified in prose."""

    kind: str  # 'power_use', 'damage_taken', 'wound', 'death', 'item_used', 'spell_cast'
    actor_ref: CharacterRef | None = None
    target_ref: CharacterRef | None = None
    description: str = ""
    evidence: str = ""
    metadata: Json = Field(default_factory=dict)


class CreationStep(BaseModel):
    id: str
    title: str
    step_schema: JsonSchema
    description: str = ""
    optional: bool = False


class TickContext(BaseModel):
    """Context passed to `MechanicsModule.time_tick`."""

    campaign_id: str
    duration: Duration
    extras: Json = Field(default_factory=dict)


class ApiVersion(StrEnum):
    V1 = "1"


class ModuleManifest(BaseModel):
    """A mechanics module's manifest.yaml in typed form."""

    id: MechanicsModuleId
    name: str
    version: str
    api_version: str = ApiVersion.V1.value
    author: str = ""
    homepage: str = ""
    description: str = ""
    sheet_kinds: list[str] = Field(default_factory=list)
    content_kinds: list[str] = Field(default_factory=list)
    capabilities: list[str] = Field(default_factory=list)
    expression_vocabulary_extensions: list[str] = Field(default_factory=list)
    ui: Json = Field(default_factory=dict)
    hud_widgets: list[HudWidget] = Field(default_factory=list)


class MissingSheet(BaseModel):
    """An entity whose sheet is missing under a target mechanics module."""

    kind: str
    entity_id: str
    character_name: str | None = None


class MechanicsSwitchResult(BaseModel):
    """Outcome of :meth:`MechanicsService.switch_module`.

    ``missing_sheets`` lists entities that have a sheet for the previous
    module but lack one for the new module; the UI uses this to drive a
    bulk character-creation wizard.
    """

    previous: str | None = None
    current: str | None = None
    missing_sheets: list[MissingSheet] = Field(default_factory=list)


class ProposalResolution(BaseModel):
    """Per-proposal disposition for the pre-roll confirmation round-trip.

    Identifies the proposal by its ``label`` (a stable per-turn identifier
    each module produces). ``accepted=False`` declines the proposal;
    ``modifications`` overrides fields like pool/difficulty/modifiers on
    accept.
    """

    label: str
    accepted: bool = True
    modifications: Json | None = None
