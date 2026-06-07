"""Tests for the create-world skill's validate_world.py.

Loads the script by path (it lives outside the grimoire package) and exercises
its validate_world() against synthetic worlds plus the real sakura-high seed.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = REPO_ROOT / ".claude" / "skills" / "create-world" / "scripts" / "validate_world.py"
SEED_WORLD = (
    REPO_ROOT / "backend" / "src" / "grimoire" / "seed" / "library" / "worlds" / "sakura-high"
)


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("validate_world", SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    # Register before exec so @dataclass annotation resolution can find the
    # module via cls.__module__ in sys.modules.
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _valid_world(root: Path) -> Path:
    """A minimal world that must validate clean."""
    world = root / "tinytown"
    _write(
        world / "world.yaml",
        "id: tinytown\nname: Tiny Town\ndefaults:\n  starting_location: square\n",
    )
    _write(
        world / "locations" / "square.md",
        "---\nid: square\nname: Square\nkind: outdoor\n---\nA dusty square.\n",
    )
    _write(
        world / "characters" / "mara.md",
        "---\nid: mara\nname: Mara\nrole: major_npc\n---\nA smuggler.\n",
    )
    _write(
        world / "greetings" / "arrival.md",
        "---\nid: arrival\nname: Arrival\nstarting_location: square\n"
        "present_characters: [mara]\n---\nYou arrive.\n",
    )
    return world


def test_valid_world_passes(tmp_path: Path) -> None:
    mod = _load()
    report = mod.validate_world(_valid_world(tmp_path))
    assert report.errors == [], report.errors
    assert report.ok is True


def test_parse_error_is_reported(tmp_path: Path) -> None:
    mod = _load()
    world = _valid_world(tmp_path)
    # Invalid role enum -> model validation error.
    _write(
        world / "characters" / "bad.md",
        "---\nid: bad\nname: Bad\nrole: wizard\n---\nNope.\n",
    )
    report = mod.validate_world(world)
    assert report.ok is False
    assert any("bad.md" in e for e in report.errors)


def test_missing_greeting_location_is_error(tmp_path: Path) -> None:
    mod = _load()
    world = _valid_world(tmp_path)
    _write(
        world / "greetings" / "arrival.md",
        "---\nid: arrival\nname: Arrival\nstarting_location: nowhere\n"
        "present_characters: [mara]\n---\nYou arrive.\n",
    )
    report = mod.validate_world(world)
    assert report.ok is False
    assert any("nowhere" in e for e in report.errors)


def test_unresolved_connection_is_warning_not_error(tmp_path: Path) -> None:
    mod = _load()
    world = _valid_world(tmp_path)
    _write(
        world / "locations" / "square.md",
        "---\nid: square\nname: Square\nkind: outdoor\n"
        "connections:\n  - to: ghost-alley\n    via: street\n---\nA dusty square.\n",
    )
    report = mod.validate_world(world)
    assert report.ok is True, report.errors
    assert any("ghost-alley" in w for w in report.warnings)


def test_bare_scalar_extras_pass(tmp_path: Path) -> None:
    # The app wraps bare-scalar extras (extras: {k: v}); the validator must too.
    mod = _load()
    world = _valid_world(tmp_path)
    _write(
        world / "characters" / "mara.md",
        "---\nid: mara\nname: Mara\nrole: major_npc\n"
        'extras:\n  favorite_drink: "rum"\n  notable_skills: ["lockpicking", "knives"]\n'
        "---\nA smuggler.\n",
    )
    report = mod.validate_world(world)
    assert report.errors == [], report.errors
    assert report.ok is True


def test_reserved_extras_key_is_error(tmp_path: Path) -> None:
    mod = _load()
    world = _valid_world(tmp_path)
    _write(
        world / "characters" / "mara.md",
        "---\nid: mara\nname: Mara\nrole: major_npc\n"
        "extras:\n  mechanics_strength: 3\n---\nA smuggler.\n",
    )
    report = mod.validate_world(world)
    assert report.ok is False
    assert any("mara.md" in e for e in report.errors)


def test_seed_world_is_error_free() -> None:
    mod = _load()
    report = mod.validate_world(SEED_WORLD)
    assert report.errors == [], report.errors
