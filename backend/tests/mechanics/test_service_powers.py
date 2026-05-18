"""Tests for §9: power_definitions / power_definition façade."""

from __future__ import annotations

import textwrap
from pathlib import Path

from grimoire.mechanics import MechanicsService

from .conftest import write_module

_POWERS_MECHANICS_PY = textwrap.dedent(
    """
    from grimoire.types.common import ValidationResult
    from grimoire.types.mechanics import PowerDefinition


    POWERS = [
        PowerDefinition(id="celerity.1", name="Celerity 1", kind="discipline", rating=1),
        PowerDefinition(id="celerity.2", name="Celerity 2", kind="discipline", rating=2),
    ]
    INDEX = {p.id: p for p in POWERS}


    class Mechanics:
        id = "vampire"
        name = "Vampire"
        version = "1.0.0"
        api_version = "1"

        def sheet_schema(self, entity_kind):
            return None

        def validate_sheet(self, entity_kind, sheet):
            return ValidationResult(valid=True)

        def initialize_sheet(self, entity_kind, entity_id):
            return {}

        def list_content_kinds(self):
            return []

        def content_schema(self, kind):
            return {}

        def capabilities_of(self, entity_ref, sheet):
            return []

        def power_definitions(self):
            return list(POWERS)

        def power_definition(self, power_id):
            return INDEX.get(power_id)

        def evaluate_pre_roll(self, player_input, scene):
            return []

        def resolve_roll(self, roll, rng_seed):
            return {"roll_id": roll.id, "dice": [], "successes": 0, "outcome": ""}

        def validate_narrated_event(self, event, scene):
            return ValidationResult(valid=True)

        def character_creation_steps(self):
            return []

        def time_tick(self, entity_ref, sheet, duration, context):
            return []

        def system_summary(self):
            return ""
    """
).strip()


async def test_power_definitions_returns_typed_list(
    service: MechanicsService, mechanics_root: Path
) -> None:
    write_module(
        mechanics_root,
        "vampire",
        mechanics_py=_POWERS_MECHANICS_PY,
    )
    await service.rescan()
    await service._state_store.upsert_campaign(
        campaign_id="c-v", name="V", mechanics_module="vampire"
    )
    powers = await service.power_definitions("c-v")
    assert {p.id for p in powers} == {"celerity.1", "celerity.2"}


async def test_power_definition_lookup_by_id(
    service: MechanicsService, mechanics_root: Path
) -> None:
    write_module(
        mechanics_root,
        "vampire",
        mechanics_py=_POWERS_MECHANICS_PY,
    )
    await service.rescan()
    await service._state_store.upsert_campaign(
        campaign_id="c-v2", name="V", mechanics_module="vampire"
    )
    power = await service.power_definition("c-v2", "celerity.2")
    assert power is not None
    assert power.rating == 2
    assert await service.power_definition("c-v2", "nope") is None


async def test_power_definitions_null_campaign(
    service: MechanicsService, mechanics_root: Path
) -> None:
    await service._state_store.upsert_campaign(
        campaign_id="c-null", name="N", mechanics_module=None
    )
    assert await service.power_definitions("c-null") == []
    assert await service.power_definition("c-null", "x") is None
