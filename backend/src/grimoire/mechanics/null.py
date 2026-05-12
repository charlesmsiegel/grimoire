"""A trivial mechanics module used for ``mechanics: null`` campaigns.

When a campaign opts out of mechanics, the façade returns this module so
callers don't need to special-case ``None``. Every query returns the empty
shape the caller would otherwise have to special-case (no schema, no
capabilities, no rolls).
"""

from __future__ import annotations

from typing import Any

from grimoire.types.common import (
    CharacterRef,
    Duration,
    JsonSchema,
    ValidationResult,
)
from grimoire.types.mechanics import (
    Capability,
    CreationStep,
    NarratedEvent,
    PowerDefinition,
    ProposedRoll,
    Roll,
    RollResult,
)
from grimoire.types.scene import SceneContext

NULL_MECHANICS_ID = "null"


class NullMechanicsModule:
    """A MechanicsModule that does nothing — for ``mechanics: null`` campaigns."""

    id: str = NULL_MECHANICS_ID
    name: str = "No mechanics"
    version: str = "0.0.0"
    api_version: str = "1"

    def sheet_schema(self, entity_kind: str) -> JsonSchema | None:
        return None

    def validate_sheet(self, entity_kind: str, sheet: dict) -> ValidationResult:
        return ValidationResult(valid=True)

    def initialize_sheet(self, entity_kind: str, entity_id: str) -> dict:
        return {}

    def list_content_kinds(self) -> list[str]:
        return []

    def content_schema(self, kind: str) -> JsonSchema:
        return {}

    def capabilities_of(self, entity_ref: CharacterRef, sheet: dict) -> list[Capability]:
        return []

    def power_definitions(self) -> list[PowerDefinition]:
        return []

    def power_definition(self, power_id: str) -> PowerDefinition | None:
        return None

    def evaluate_pre_roll(self, player_input: str, scene: SceneContext) -> list[ProposedRoll]:
        return []

    def resolve_roll(self, roll: Roll, rng_seed: int) -> RollResult:
        return RollResult(roll_id=roll.id, dice=[], successes=0, outcome="no mechanics")

    def validate_narrated_event(
        self, event: NarratedEvent, scene: SceneContext
    ) -> ValidationResult:
        return ValidationResult(valid=True)

    def character_creation_steps(self) -> list[CreationStep]:
        return []

    def time_tick(
        self,
        entity_ref: CharacterRef,
        sheet: dict,
        duration: Duration,
        context: Any,
    ) -> list[Any]:
        return []

    def system_summary(self) -> str:
        return "No mechanics — pure narrative play."


__all__ = ["NULL_MECHANICS_ID", "NullMechanicsModule"]
