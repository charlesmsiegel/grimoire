"""Tests for the Mechanics façade service."""

from __future__ import annotations

from pathlib import Path

import pytest

from grimoire.mechanics import NULL_MECHANICS_ID, MechanicsConfig, MechanicsService
from grimoire.state_store import StateStore
from grimoire.state_store.errors import NotFoundError
from grimoire.types.mechanics import Roll
from grimoire.types.scene import Scene, SceneContext

from .conftest import write_module


def _scene(campaign_id: str = "c1") -> SceneContext:
    scene = Scene(
        id="scene-1",
        campaign_id=campaign_id,
        branch_id=f"{campaign_id}:main",
        ordinal=1,
        slug="opening",
        file_path="scenes/0001-opening.md",
    )
    return SceneContext(scene=scene)


async def _seed(store: StateStore, module_id: str | None) -> None:
    await store.upsert_campaign(
        campaign_id="c1",
        name="Test",
        mechanics_module=module_id,
    )


async def test_rescan_loads_a_module(service: MechanicsService, mechanics_root: Path) -> None:
    write_module(mechanics_root, "wod")
    report = await service.rescan()
    assert report.loaded == ["wod"]
    assert report.failed == []
    assert (await service.module_info("wod")).id == "wod"


async def test_rescan_records_load_failures(
    service: MechanicsService, mechanics_root: Path
) -> None:
    write_module(mechanics_root, "good")
    write_module(mechanics_root, "bad", mechanics_py="raise RuntimeError('boom')\n")
    report = await service.rescan()
    assert "good" in report.loaded
    assert any(mid == "bad" for mid, _ in report.failed)
    assert "bad" in service.failed_modules()


async def test_rescan_removes_vanished_modules(
    service: MechanicsService, mechanics_root: Path
) -> None:
    write_module(mechanics_root, "ephemeral")
    await service.rescan()
    assert service.get_module("ephemeral") is not None

    import shutil

    shutil.rmtree(mechanics_root / "ephemeral")
    report = await service.rescan()
    assert "ephemeral" in report.removed
    assert service.get_module("ephemeral") is None


async def test_active_module_returns_none_for_null_campaign(
    service: MechanicsService, store: StateStore
) -> None:
    await _seed(store, None)
    assert await service.active_module("c1") is None
    # Convenience pass-throughs return empty/None for null campaigns.
    assert await service.sheet_schema("c1", "character") is None
    assert await service.capabilities_of("c1", "character:winifred") == []
    assert await service.evaluate_pre_roll("c1", "I climb the wall", _scene()) == []


async def test_active_module_returns_none_for_unknown_campaign(
    service: MechanicsService,
) -> None:
    with pytest.raises(NotFoundError):
        await service.active_module("nope")


async def test_explicit_null_sentinel_treated_as_null(
    service: MechanicsService, store: StateStore
) -> None:
    await _seed(store, NULL_MECHANICS_ID)
    assert await service.active_module("c1") is None


async def test_active_module_returns_loaded_instance(
    service: MechanicsService, store: StateStore, mechanics_root: Path
) -> None:
    write_module(mechanics_root, "wod")
    await service.rescan()
    await _seed(store, "wod")
    module = await service.active_module("c1")
    assert module is not None
    assert module.id == "wod"


async def test_sheet_schema_returns_module_schema(
    service: MechanicsService, store: StateStore, mechanics_root: Path
) -> None:
    write_module(mechanics_root, "wod")
    await service.rescan()
    await _seed(store, "wod")
    schema = await service.sheet_schema("c1", "character")
    assert schema is not None
    assert schema["properties"]["vitality"]["type"] == "integer"


async def test_update_sheet_creates_then_merges(
    service: MechanicsService, store: StateStore, mechanics_root: Path
) -> None:
    write_module(mechanics_root, "wod")
    await service.rescan()
    await _seed(store, "wod")

    sheet = await service.update_sheet(
        "c1",
        "character:winifred",
        {"name": "winifred", "vitality": 3},
        source="user",
    )
    assert sheet["name"] == "winifred"
    assert sheet["vitality"] == 3

    # Patch merges over existing.
    updated = await service.update_sheet(
        "c1",
        "character:winifred",
        {"vitality": 2},
        source="user",
    )
    assert updated["name"] == "winifred"
    assert updated["vitality"] == 2

    # And get_sheet returns the persisted state.
    fetched = await service.get_sheet("c1", "character:winifred")
    assert fetched == updated


async def test_update_sheet_validates_strict(
    service: MechanicsService, store: StateStore, mechanics_root: Path
) -> None:
    write_module(mechanics_root, "wod")
    await service.rescan()
    await _seed(store, "wod")

    with pytest.raises(ValueError) as info:
        await service.update_sheet(
            "c1",
            "character:winifred",
            {"name": 12345},  # validate_sheet rejects non-string name
            source="user",
        )
    assert "validation" in str(info.value)


async def test_update_sheet_rejects_null_mechanics(
    service: MechanicsService, store: StateStore
) -> None:
    await _seed(store, None)
    with pytest.raises(ValueError):
        await service.update_sheet("c1", "character:x", {"name": "X"})


async def test_get_sheet_returns_none_when_missing(
    service: MechanicsService, store: StateStore, mechanics_root: Path
) -> None:
    write_module(mechanics_root, "wod")
    await service.rescan()
    await _seed(store, "wod")
    assert await service.get_sheet("c1", "character:nobody") is None


async def test_capabilities_use_stored_sheet(
    service: MechanicsService, store: StateStore, mechanics_root: Path
) -> None:
    write_module(mechanics_root, "wod")
    await service.rescan()
    await _seed(store, "wod")

    # No sheet → empty capabilities.
    assert await service.capabilities_of("c1", "character:winifred") == []

    await service.update_sheet("c1", "character:winifred", {"name": "winifred", "vitality": 5})
    caps = await service.capabilities_of("c1", "character:winifred")
    assert [c.id if hasattr(c, "id") else c["id"] for c in caps] == ["test.move"]


async def test_evaluate_pre_roll_delegates(
    service: MechanicsService, store: StateStore, mechanics_root: Path
) -> None:
    write_module(mechanics_root, "wod")
    await service.rescan()
    await _seed(store, "wod")
    rolls = await service.evaluate_pre_roll("c1", "I climb the wall", _scene())
    assert len(rolls) == 1
    proposed = rolls[0]
    label = proposed.label if hasattr(proposed, "label") else proposed["label"]
    assert label == "Climb"


async def test_resolve_roll_deterministic_per_branch(
    service: MechanicsService, store: StateStore, mechanics_root: Path
) -> None:
    write_module(mechanics_root, "wod")
    await service.rescan()
    await _seed(store, "wod")
    roll = Roll(id="r1", kind="dice-pool", pool=5, seed=7, difficulty=6)
    first = await service.resolve_roll("c1", roll)
    second = await service.resolve_roll("c1", roll)
    assert _dice(first) == _dice(second)


async def test_resolve_roll_differs_across_branches(
    service: MechanicsService, store: StateStore, mechanics_root: Path
) -> None:
    write_module(mechanics_root, "wod")
    await service.rescan()
    await _seed(store, "wod")
    # Add a second branch to compare against.
    await store.fork_branch(
        campaign_id="c1",
        parent_branch_id="c1:main",
        new_label="alt",
    )
    roll = Roll(id="r1", kind="dice-pool", pool=5, seed=7, difficulty=6)
    main = await service.resolve_roll("c1", roll, branch_id="c1:main")
    alt = await service.resolve_roll("c1", roll, branch_id="c1:alt")
    assert _dice(main) != _dice(alt)


async def test_branch_seed_fallback_is_stable_across_processes(
    service: MechanicsService, store: StateStore, mechanics_root: Path
) -> None:
    """When the branch row is missing, the seed must still be deterministic.

    Python's built-in ``hash()`` is randomised per-process via PYTHONHASHSEED,
    so the fallback must not rely on it.
    """
    import os
    import subprocess
    import sys

    write_module(mechanics_root, "wod")
    await service.rescan()
    await _seed(store, "wod")
    roll = Roll(id="r1", kind="dice-pool", pool=5, seed=7, difficulty=6)
    first = await service.resolve_roll("c1", roll, branch_id="c1:never-forked")
    in_process_dice = _dice(first)

    # Re-derive in a child process with a different PYTHONHASHSEED to
    # prove the fallback doesn't lean on the randomised builtin hash.
    script = (
        "from grimoire.mechanics.rng import derive_roll_seed;"
        "import hashlib;"
        "tid = 'c1:never-forked';"
        "digest = hashlib.sha256(tid.encode()).digest();"
        "bs = int.from_bytes(digest[:8], 'big') & 0x7FFFFFFFFFFFFFFF;"
        "print(derive_roll_seed(bs, 7, 'r1'))"
    )
    env: dict[str, str] = {"PYTHONHASHSEED": "12345", "PATH": ""}
    for k in ("SystemRoot", "SYSTEMDRIVE", "TEMP", "TMP"):
        v = os.environ.get(k)
        if v:
            env[k] = v
    out = subprocess.check_output(
        [sys.executable, "-c", script],
        env=env,
        text=True,
    )
    expected_seed = int(out.strip())
    # Reproduce the test mechanics module's `resolve_roll`:
    expected_dice = [((expected_seed >> i) & 0xFF) % 10 + 1 for i in range(5)]
    assert in_process_dice == expected_dice


async def test_rescan_discovered_uses_dir_name_when_manifest_id_null(
    service: MechanicsService, mechanics_root: Path
) -> None:
    """`id: null` in manifest must not leak ``None`` into RescanReport."""
    write_module(
        mechanics_root,
        "well-formed",
        manifest={"id": None, "name": "Anonymous"},
    )
    report = await service.rescan()
    assert all(isinstance(x, str) for x in report.discovered)
    assert "well-formed" in report.discovered


async def test_resolve_roll_null_module_returns_no_dice(
    service: MechanicsService, store: StateStore
) -> None:
    await _seed(store, None)
    roll = Roll(id="r1", kind="dice-pool", pool=5, seed=7)
    result = await service.resolve_roll("c1", roll)
    assert _dice(result) == []


async def test_list_installed_modules_round_trip(
    service: MechanicsService, mechanics_root: Path
) -> None:
    write_module(mechanics_root, "wod")
    write_module(mechanics_root, "ars")
    await service.rescan()
    installed = await service.list_installed_modules()
    assert {m.id for m in installed} == {"wod", "ars"}
    assert await service.module_info("missing") is None


async def test_register_module_works_in_process(
    service: MechanicsService, store: StateStore
) -> None:
    from grimoire.mechanics import NullMechanicsModule
    from grimoire.types.mechanics import ModuleManifest

    instance = NullMechanicsModule()
    instance.id = "in-proc"  # type: ignore[attr-defined]
    manifest = ModuleManifest(
        id="in-proc",
        name="In Proc",
        version="0.0.1",
        api_version="1",
    )
    service.register_module(manifest, instance)
    await _seed(store, "in-proc")
    assert (await service.active_module("c1")).id == "in-proc"


async def test_unknown_module_id_logs_and_returns_none(
    service: MechanicsService, store: StateStore
) -> None:
    await _seed(store, "not-loaded")
    assert await service.active_module("c1") is None


# ----------------------------------------------------------------------
# helpers
# ----------------------------------------------------------------------


def _dice(result) -> list[int]:
    return result.dice if hasattr(result, "dice") else result["dice"]


# A separate config with strict_events=True so we can assert the strict path.
async def test_validate_narrated_event_lenient_downgrades_errors(
    store: StateStore, mechanics_root: Path
) -> None:
    write_module(
        mechanics_root,
        "strict-test",
        mechanics_py=_strict_mechanics_py(),
    )
    config = MechanicsConfig(root=mechanics_root)
    svc = MechanicsService(config=config, state_store=store)
    await svc.rescan()
    await store.upsert_campaign(campaign_id="c1", name="Test", mechanics_module="strict-test")
    from grimoire.types.mechanics import NarratedEvent

    event = NarratedEvent(kind="power_use", description="fake power")
    result = await svc.validate_narrated_event("c1", event, _scene())
    # Lenient: still valid, errors become warnings.
    assert result.valid is True
    assert any("unknown" in w for w in result.warnings)


def _strict_mechanics_py() -> str:
    import textwrap

    return textwrap.dedent(
        """
        from grimoire.types.common import ValidationResult


        class Mechanics:
            id = "strict-test"
            name = "Strict"
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
                return {"roll_id": roll.id, "dice": [], "successes": 0}

            def validate_narrated_event(self, event, scene):
                return ValidationResult(valid=False, errors=["unknown power"])

            def character_creation_steps(self):
                return []

            def time_tick(self, entity_ref, sheet, duration, context):
                return []

            def system_summary(self):
                return "strict"
        """
    ).strip()
