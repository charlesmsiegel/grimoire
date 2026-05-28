"""Shared fixtures for mechanics tests.

`write_module` builds a complete mechanics module directory (manifest +
mechanics.py) on disk so each test can express only the unusual bits it
cares about.
"""

from __future__ import annotations

import textwrap
from pathlib import Path
from typing import Any

import pytest
import yaml

from grimoire.mechanics import MechanicsConfig, MechanicsService
from grimoire.state_store import StateStore
from grimoire.storage import Database
from grimoire.testing.db_template import stamp_migrated_db


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def write_module(
    root: Path,
    module_id: str,
    *,
    manifest: dict[str, Any] | None = None,
    mechanics_py: str | None = None,
    omit_mechanics_py: bool = False,
    sheets: dict[str, dict] | None = None,
    content: dict[str, dict] | None = None,
    theme_css: str | None = None,
) -> Path:
    """Materialise a mechanics module directory and return its path."""
    import json

    module_dir = root / module_id
    module_dir.mkdir(parents=True, exist_ok=True)

    base_manifest = {
        "id": module_id,
        "name": module_id.replace("-", " ").title(),
        "version": "1.0.0",
        "api_version": "1",
        "sheet_kinds": ["character"],
    }
    full_manifest = {**base_manifest, **(manifest or {})}
    (module_dir / "manifest.yaml").write_text(
        yaml.safe_dump(full_manifest, sort_keys=False), encoding="utf-8"
    )

    if not omit_mechanics_py:
        body = mechanics_py or default_mechanics_py(module_id)
        (module_dir / "mechanics.py").write_text(body, encoding="utf-8")

    if sheets:
        sheet_dir = module_dir / "sheets"
        sheet_dir.mkdir(exist_ok=True)
        for kind, schema in sheets.items():
            (sheet_dir / f"{kind}.json").write_text(json.dumps(schema), encoding="utf-8")

    if content:
        content_dir = module_dir / "content"
        content_dir.mkdir(exist_ok=True)
        for kind, schema in content.items():
            (content_dir / f"{kind}.json").write_text(json.dumps(schema), encoding="utf-8")

    if theme_css is not None:
        (module_dir / "theme.css").write_text(theme_css, encoding="utf-8")
    return module_dir


def default_mechanics_py(module_id: str) -> str:
    """A minimal MechanicsModule whose ``id`` matches ``module_id``."""
    return textwrap.dedent(
        f'''
        from grimoire.types.common import ValidationResult


        class Mechanics:
            id = "{module_id}"
            name = "Test Mechanics"
            version = "1.0.0"
            api_version = "1"

            def sheet_schema(self, entity_kind):
                if entity_kind == "character":
                    return {{
                        "type": "object",
                        "properties": {{
                            "name": {{"type": "string"}},
                            "vitality": {{"type": "integer", "minimum": 0}},
                        }},
                    }}
                return None

            def validate_sheet(self, entity_kind, sheet):
                if entity_kind == "character" and not isinstance(sheet.get("name", ""), str):
                    return ValidationResult(valid=False, errors=["name must be a string"])
                return ValidationResult(valid=True)

            def initialize_sheet(self, entity_kind, entity_id):
                return {{"name": entity_id, "vitality": 1}}

            def list_content_kinds(self):
                return []

            def content_schema(self, kind):
                return {{}}

            def capabilities_of(self, entity_ref, sheet):
                vit = sheet.get("vitality", 0)
                if vit <= 0:
                    return []
                return [{{"id": "test.move", "name": "Move", "kind": "feat"}}]

            def power_definitions(self):
                return []

            def power_definition(self, power_id):
                return None

            def evaluate_pre_roll(self, player_input, scene):
                if "climb" in player_input.lower():
                    return [{{
                        "label": "Climb",
                        "kind": "dice-pool",
                        "pool": 4,
                    }}]
                return []

            def resolve_roll(self, roll, rng_seed):
                # Deterministic: dice values derived from (pool, rng_seed).
                dice = [((rng_seed >> i) & 0xFF) % 10 + 1 for i in range(roll.pool)]
                successes = sum(1 for d in dice if d >= (roll.difficulty or 6))
                return {{
                    "roll_id": roll.id,
                    "dice": dice,
                    "successes": successes,
                    "outcome": f"{{successes}} successes",
                    "narration_hint": "",
                }}

            def validate_narrated_event(self, event, scene):
                return ValidationResult(valid=True)

            def character_creation_steps(self):
                return []

            def time_tick(self, entity_ref, sheet, duration, context):
                return []

            def system_summary(self):
                return "Test mechanics."
        '''
    ).strip()


@pytest.fixture
def mechanics_root(tmp_path: Path) -> Path:
    root = tmp_path / "mechanics"
    root.mkdir()
    return root


@pytest.fixture
async def store(tmp_path: Path):
    data_root = tmp_path / "data"
    data_root.mkdir()
    db = Database(stamp_migrated_db(tmp_path / "campaigns.sqlite"), pool_size=2)
    await db.connect()
    s = StateStore(db, data_root)
    try:
        yield s
    finally:
        await db.close()


@pytest.fixture
async def service(mechanics_root: Path, store: StateStore):
    config = MechanicsConfig(root=mechanics_root)
    return MechanicsService(config=config, state_store=store)


__all__ = ["default_mechanics_py", "write_module"]
