from __future__ import annotations

import json

import pytest
import yaml

from grimoire.mechanics.authoring import (
    InvalidIdentifierError,
    ManifestValidationError,
    MechanicsAuthor,
    ModuleExistsError,
    ModuleNotFoundError,
    SchemaValidationError,
    generate_mechanics_py,
)

# --------------------------------------------------------------------------- #
# Task 1: stub generator
# --------------------------------------------------------------------------- #


def test_generated_stub_contains_identity_and_subclass():
    src = generate_mechanics_py(
        module_id="my-system",
        name="My System",
        version="1.2.3",
        api_version="1",
        description="A test system.",
    )
    assert "class Mechanics(DiskBackedMechanicsModule):" in src
    assert 'id = "my-system"' in src
    assert 'version = "1.2.3"' in src
    # All behavioral protocol methods the base class does NOT provide are present.
    for method in [
        "def validate_sheet",
        "def initialize_sheet",
        "def capabilities_of",
        "def power_definitions",
        "def power_definition",
        "def evaluate_pre_roll",
        "def resolve_roll",
        "def validate_narrated_event",
        "def character_creation_steps",
        "def time_tick",
        "def system_summary",
    ]:
        assert method in src


def test_generated_stub_is_valid_python():
    src = generate_mechanics_py(
        module_id="x", name="X", version="1.0.0", api_version="1", description=""
    )
    compile(src, "<stub>", "exec")  # raises SyntaxError if malformed


# --------------------------------------------------------------------------- #
# Task 2: scaffold
# --------------------------------------------------------------------------- #


async def test_scaffold_creates_module_that_loads_green(service):
    author = MechanicsAuthor(service)
    report = await author.scaffold(
        {
            "id": "acme",
            "name": "Acme System",
            "version": "1.0.0",
            "api_version": "1",
            "sheet_kinds": ["character"],
            "content_kinds": ["spells"],
        }
    )
    root = service.config.root
    assert (root / "acme" / "manifest.yaml").is_file()
    assert (root / "acme" / "mechanics.py").is_file()
    assert (root / "acme" / "sheets" / "character.json").is_file()
    assert (root / "acme" / "content" / "spells.json").is_file()
    # Loads without error: appears in loaded, not failed.
    assert "acme" in report.loaded
    assert all(mid != "acme" for mid, _ in report.failed)


async def test_scaffold_refuses_existing_id(service):
    author = MechanicsAuthor(service)
    spec = {"id": "dup", "name": "Dup", "version": "1.0.0", "api_version": "1"}
    await author.scaffold(spec)
    with pytest.raises(ModuleExistsError):
        await author.scaffold(spec)


async def test_scaffold_rejects_invalid_manifest(service):
    author = MechanicsAuthor(service)
    with pytest.raises(ManifestValidationError) as exc:
        await author.scaffold({"id": "Bad Id!", "name": "", "version": "x"})
    assert exc.value.errors  # carries human-readable messages


async def test_scaffold_bad_kind_leaves_no_partial_module(service):
    author = MechanicsAuthor(service)
    with pytest.raises(InvalidIdentifierError):
        await author.scaffold(
            {
                "id": "partial",
                "name": "Partial",
                "version": "1.0.0",
                "api_version": "1",
                "sheet_kinds": ["Bad Kind!"],
            }
        )
    # Nothing must be written when validation fails — a retry would otherwise
    # hit "already exists".
    assert not (service.config.root / "partial").exists()


async def test_scaffold_creates_nested_theme_path(service):
    author = MechanicsAuthor(service)
    report = await author.scaffold(
        {
            "id": "nested",
            "name": "Nested",
            "version": "1.0.0",
            "api_version": "1",
            "ui": {"theme_css": "styles/theme.css"},
        }
    )
    assert (service.config.root / "nested" / "styles" / "theme.css").is_file()
    assert "nested" in report.loaded


async def test_write_theme_css_creates_nested_parent(service):
    author = MechanicsAuthor(service)
    await author.scaffold(
        {
            "id": "nested2",
            "name": "Nested2",
            "version": "1.0.0",
            "api_version": "1",
            "ui": {"theme_css": "styles/theme.css"},
        }
    )
    await author.write_theme_css("nested2", ".x { color: blue; }")
    assert (service.config.root / "nested2" / "styles" / "theme.css").read_text(
        encoding="utf-8"
    ) == ".x { color: blue; }"


# --------------------------------------------------------------------------- #
# Task 3: edit methods + guards
# --------------------------------------------------------------------------- #


async def _scaffolded(service):
    author = MechanicsAuthor(service)
    await author.scaffold(
        {
            "id": "edit-me",
            "name": "Edit Me",
            "version": "1.0.0",
            "api_version": "1",
            "sheet_kinds": ["character"],
            "content_kinds": ["spells"],
            "ui": {"theme_css": "theme.css"},
        }
    )
    return author


async def test_write_sheet_schema_persists_and_keeps_mechanics_py(service):
    author = await _scaffolded(service)
    root = service.config.root
    before = (root / "edit-me" / "mechanics.py").read_text(encoding="utf-8")
    schema = {"type": "object", "properties": {"hp": {"type": "integer"}}}
    await author.write_sheet_schema("edit-me", "character", schema)
    on_disk = json.loads((root / "edit-me" / "sheets" / "character.json").read_text())
    assert on_disk == schema
    assert (root / "edit-me" / "mechanics.py").read_text(encoding="utf-8") == before


async def test_write_content_schema_persists(service):
    author = await _scaffolded(service)
    schema = {"type": "object", "properties": {"level": {"type": "integer"}}}
    await author.write_content_schema("edit-me", "spells", schema)
    on_disk = json.loads((service.config.root / "edit-me" / "content" / "spells.json").read_text())
    assert on_disk == schema


async def test_write_theme_css_persists(service):
    author = await _scaffolded(service)
    await author.write_theme_css("edit-me", ".sheet { color: red; }")
    assert (service.config.root / "edit-me" / "theme.css").read_text(
        encoding="utf-8"
    ) == ".sheet { color: red; }"


async def test_write_manifest_updates_name(service):
    author = await _scaffolded(service)
    await author.write_manifest(
        "edit-me",
        {"id": "edit-me", "name": "Renamed", "version": "2.0.0", "api_version": "1"},
    )
    data = yaml.safe_load((service.config.root / "edit-me" / "manifest.yaml").read_text())
    assert data["name"] == "Renamed"
    assert data["version"] == "2.0.0"


async def test_edit_missing_module_raises(service):
    author = MechanicsAuthor(service)
    with pytest.raises(ModuleNotFoundError):
        await author.write_theme_css("ghost", "x {}")


async def test_invalid_schema_rejected(service):
    author = await _scaffolded(service)
    with pytest.raises(SchemaValidationError):
        await author.write_sheet_schema("edit-me", "character", {"type": 123})


async def test_bad_kind_rejected(service):
    author = await _scaffolded(service)
    with pytest.raises(InvalidIdentifierError):
        await author.write_sheet_schema("edit-me", "../escape", {"type": "object"})


# --------------------------------------------------------------------------- #
# Task 4: service exposes author
# --------------------------------------------------------------------------- #


def test_service_exposes_author(service):
    from grimoire.mechanics import MechanicsAuthor as ExportedAuthor

    assert isinstance(service.author, ExportedAuthor)
    assert service.author is service.author  # cached
