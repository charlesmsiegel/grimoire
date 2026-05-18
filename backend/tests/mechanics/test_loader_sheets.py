"""Tests for §1: disk-loaded sheet schemas."""

from __future__ import annotations

import textwrap
from pathlib import Path

from grimoire.mechanics import MechanicsService
from grimoire.mechanics.discovery import discover
from grimoire.mechanics.loader import load_module

from .conftest import write_module


def _discover_one(root: Path, module_id: str):
    found, _ = discover([root])
    return next(d for d in found if d.module_dir.name == module_id)


def test_loader_reads_sheet_schema(mechanics_root: Path) -> None:
    write_module(
        mechanics_root,
        "with-sheets",
        sheets={
            "character": {
                "type": "object",
                "properties": {"name": {"type": "string"}},
            }
        },
    )
    result = load_module(_discover_one(mechanics_root, "with-sheets"))
    assert result.ok, result.errors
    assert "character" in result.sheet_schemas
    assert result.sheet_schemas["character"]["properties"]["name"]["type"] == "string"
    assert result.warnings == []


def test_loader_warns_when_declared_sheet_kind_is_missing(mechanics_root: Path) -> None:
    write_module(
        mechanics_root,
        "missing-sheets",
        manifest={"sheet_kinds": ["character", "item"]},
        # Don't ship a sheets/ directory at all.
    )
    result = load_module(_discover_one(mechanics_root, "missing-sheets"))
    assert result.ok, result.errors
    assert result.sheet_schemas == {}
    assert any("character" in w for w in result.warnings)
    assert any("item" in w for w in result.warnings)


def test_loader_warns_on_invalid_sheet_schema(mechanics_root: Path) -> None:
    write_module(
        mechanics_root,
        "broken-sheet",
        sheets={"character": {"type": 123}},  # invalid JSON Schema (type must be string/array)
    )
    result = load_module(_discover_one(mechanics_root, "broken-sheet"))
    assert result.ok, result.errors  # module load still succeeds
    assert "character" not in result.sheet_schemas
    assert any("character" in w and "invalid" in w.lower() for w in result.warnings)


async def test_service_falls_back_to_registry_when_module_returns_none(
    mechanics_root: Path, service: MechanicsService
) -> None:
    """The façade should consult ``RegisteredModule.sheet_schemas`` when the
    module instance's ``sheet_schema`` returns ``None``."""
    mechanics_py = textwrap.dedent(
        """
        from grimoire.types.common import ValidationResult


        class Mechanics:
            id = "fallback"
            name = "Fallback"
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
    write_module(
        mechanics_root,
        "fallback",
        mechanics_py=mechanics_py,
        sheets={"character": {"type": "object", "properties": {"hp": {"type": "integer"}}}},
    )
    await service.rescan()
    await service._state_store.upsert_campaign(
        campaign_id="c-fallback", name="C", mechanics_module="fallback"
    )
    schema = await service.sheet_schema("c-fallback", "character")
    assert schema is not None
    assert schema["properties"]["hp"]["type"] == "integer"
