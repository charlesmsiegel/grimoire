"""Tests for §4: character creation steps + finalize."""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from grimoire.mechanics import MechanicsService

from .conftest import write_module

_CREATION_MECHANICS_PY = textwrap.dedent(
    """
    from grimoire.types.common import ValidationResult
    from grimoire.types.mechanics import CreationStep


    class Mechanics:
        id = "creator"
        name = "Creator"
        version = "1.0.0"
        api_version = "1"

        def sheet_schema(self, entity_kind):
            if entity_kind == "character":
                return {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "attributes": {"type": "object"},
                    },
                    "required": ["name"],
                }
            return None

        def validate_sheet(self, entity_kind, sheet):
            if not isinstance(sheet.get("name"), str):
                return ValidationResult(valid=False, errors=["name required"])
            return ValidationResult(valid=True)

        def initialize_sheet(self, entity_kind, entity_id):
            return {"name": entity_id}

        def list_content_kinds(self):
            return []

        def content_schema(self, kind):
            return {}

        def capabilities_of(self, entity_ref, sheet):
            return []

        def power_definitions(self):
            return []

        def power_definition(self, power_id):
            return None

        def evaluate_pre_roll(self, player_input, scene):
            return []

        def resolve_roll(self, roll, rng_seed):
            return {"roll_id": roll.id, "dice": [], "successes": 0, "outcome": ""}

        def validate_narrated_event(self, event, scene):
            return ValidationResult(valid=True)

        def character_creation_steps(self):
            return [
                CreationStep(
                    id="identity",
                    title="Identity",
                    step_schema={
                        "type": "object",
                        "properties": {"name": {"type": "string"}},
                        "required": ["name"],
                    },
                ),
                CreationStep(
                    id="attributes",
                    title="Attributes",
                    step_schema={
                        "type": "object",
                        "properties": {
                            "attributes": {
                                "type": "object",
                                "properties": {"str": {"type": "integer"}},
                            },
                        },
                    },
                ),
            ]

        def time_tick(self, entity_ref, sheet, duration, context):
            return []

        def system_summary(self):
            return ""
    """
).strip()


async def _setup(service: MechanicsService, mechanics_root: Path) -> None:
    write_module(
        mechanics_root,
        "creator",
        mechanics_py=_CREATION_MECHANICS_PY,
    )
    await service.rescan()
    await service._state_store.upsert_campaign(
        campaign_id="c-creator", name="C", mechanics_module="creator"
    )


async def test_creation_steps_via_campaign_id(
    service: MechanicsService, mechanics_root: Path
) -> None:
    await _setup(service, mechanics_root)
    steps = await service.character_creation_steps("c-creator")
    assert [s.id for s in steps] == ["identity", "attributes"]


async def test_creation_steps_via_module_id(
    service: MechanicsService, mechanics_root: Path
) -> None:
    await _setup(service, mechanics_root)
    steps = await service.character_creation_steps("creator")
    assert [s.id for s in steps] == ["identity", "attributes"]


async def test_finalize_character_creation_persists_sheet(
    service: MechanicsService, mechanics_root: Path
) -> None:
    await _setup(service, mechanics_root)
    sheet = await service.finalize_character_creation(
        "c-creator",
        "character:hero",
        {
            "identity": {"name": "Hero"},
            "attributes": {"attributes": {"str": 3}},
        },
    )
    assert sheet["name"] == "Hero"
    assert sheet["attributes"]["str"] == 3
    # Persisted; round-trip via state_store directly.
    stored = await service._state_store.get_sheet(
        campaign_id="c-creator", kind="character", entity_id="hero", mechanics_id="creator"
    )
    assert stored == sheet


async def test_finalize_rejects_bad_step_data(
    service: MechanicsService, mechanics_root: Path
) -> None:
    await _setup(service, mechanics_root)
    with pytest.raises(ValueError):
        await service.finalize_character_creation(
            "c-creator",
            "character:hero",
            {"identity": {"name": 12}, "attributes": {}},  # name must be str
        )


async def test_finalize_rejects_null_mechanics_campaign(
    service: MechanicsService, mechanics_root: Path
) -> None:
    await _setup(service, mechanics_root)
    await service._state_store.upsert_campaign(
        campaign_id="c-null", name="N", mechanics_module=None
    )
    with pytest.raises(ValueError):
        await service.finalize_character_creation(
            "c-null", "character:x", {"identity": {"name": "x"}}
        )
