"""Tests for §2: MechanicsService content list/get/put with validation."""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from grimoire.mechanics import MechanicsService

from .conftest import write_module

_CONTENT_MECHANICS_PY = textwrap.dedent(
    """
    from grimoire.types.common import ValidationResult


    class Mechanics:
        id = "content-sys"
        name = "Content Sys"
        version = "1.0.0"
        api_version = "1"

        def sheet_schema(self, entity_kind):
            return None

        def validate_sheet(self, entity_kind, sheet):
            return ValidationResult(valid=True)

        def initialize_sheet(self, entity_kind, entity_id):
            return {}

        def list_content_kinds(self):
            return ["spell"]

        def content_schema(self, kind):
            if kind == "spell":
                return {
                    "type": "object",
                    "properties": {"name": {"type": "string"}, "level": {"type": "integer"}},
                    "required": ["name"],
                    "additionalProperties": False,
                }
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
            return []

        def time_tick(self, entity_ref, sheet, duration, context):
            return []

        def system_summary(self):
            return ""
    """
).strip()


async def _setup(service: MechanicsService, mechanics_root: Path) -> None:
    write_module(
        mechanics_root,
        "content-sys",
        manifest={"sheet_kinds": [], "content_kinds": ["spell"]},
        mechanics_py=_CONTENT_MECHANICS_PY,
    )
    await service.rescan()
    await service._state_store.upsert_campaign(
        campaign_id="c-content", name="C", mechanics_module="content-sys"
    )


async def test_list_content_kinds_via_facade(
    service: MechanicsService, mechanics_root: Path
) -> None:
    await _setup(service, mechanics_root)
    kinds = await service.list_content_kinds("c-content")
    assert kinds == ["spell"]


async def test_content_schema_via_facade(service: MechanicsService, mechanics_root: Path) -> None:
    await _setup(service, mechanics_root)
    schema = await service.content_schema("c-content", "spell")
    assert schema is not None
    assert "name" in schema["properties"]


async def test_put_and_get_content_roundtrip(
    service: MechanicsService, mechanics_root: Path
) -> None:
    await _setup(service, mechanics_root)
    payload = {"name": "Fireball", "level": 3}
    stored = await service.put_content("c-content", "spell", "fireball", payload)
    assert stored == payload

    listed = await service.list_content("c-content", "spell")
    assert len(listed) == 1
    assert listed[0]["content_id"] == "fireball"
    assert listed[0]["payload"]["name"] == "Fireball"

    fetched = await service.get_content("c-content", "spell", "fireball")
    assert fetched == payload


async def test_put_content_rejects_invalid_under_strict_mode(
    service: MechanicsService, mechanics_root: Path
) -> None:
    await _setup(service, mechanics_root)
    with pytest.raises(ValueError):
        await service.put_content("c-content", "spell", "bad", {"level": 1})  # missing 'name'


async def test_put_content_null_campaign_rejected(
    service: MechanicsService, mechanics_root: Path
) -> None:
    await _setup(service, mechanics_root)
    await service._state_store.upsert_campaign(
        campaign_id="c-null", name="N", mechanics_module=None
    )
    with pytest.raises(ValueError):
        await service.put_content("c-null", "spell", "x", {"name": "x"})


async def test_list_content_kinds_null_campaign_returns_empty(
    service: MechanicsService, mechanics_root: Path
) -> None:
    await service._state_store.upsert_campaign(
        campaign_id="c-null", name="N", mechanics_module=None
    )
    assert await service.list_content_kinds("c-null") == []
    assert await service.content_schema("c-null", "spell") is None
