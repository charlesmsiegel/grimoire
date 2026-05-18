"""Tests for §2: content schema loading."""

from __future__ import annotations

from pathlib import Path

from grimoire.mechanics.discovery import discover
from grimoire.mechanics.loader import load_module

from .conftest import write_module


def _discover_one(root: Path, module_id: str):
    found, _ = discover([root])
    return next(d for d in found if d.module_dir.name == module_id)


def test_loader_reads_content_schema(mechanics_root: Path) -> None:
    write_module(
        mechanics_root,
        "with-content",
        manifest={"sheet_kinds": [], "content_kinds": ["spell"]},
        content={
            "spell": {
                "type": "object",
                "properties": {"name": {"type": "string"}, "cost": {"type": "integer"}},
                "required": ["name"],
            }
        },
    )
    result = load_module(_discover_one(mechanics_root, "with-content"))
    assert result.ok, result.errors
    assert "spell" in result.content_schemas
    assert "name" in result.content_schemas["spell"]["properties"]
    assert result.warnings == []


def test_loader_warns_on_missing_content_file(mechanics_root: Path) -> None:
    write_module(
        mechanics_root,
        "missing-content",
        manifest={"sheet_kinds": [], "content_kinds": ["spell", "item"]},
        # Provide one but not the other.
        content={"spell": {"type": "object"}},
    )
    result = load_module(_discover_one(mechanics_root, "missing-content"))
    assert result.ok, result.errors
    assert "spell" in result.content_schemas
    assert "item" not in result.content_schemas
    assert any("item" in w for w in result.warnings)


def test_loader_warns_on_invalid_content_schema(mechanics_root: Path) -> None:
    write_module(
        mechanics_root,
        "broken-content",
        manifest={"sheet_kinds": [], "content_kinds": ["spell"]},
        content={"spell": {"type": 123}},
    )
    result = load_module(_discover_one(mechanics_root, "broken-content"))
    assert result.ok, result.errors
    assert "spell" not in result.content_schemas
    assert any("spell" in w and "invalid" in w.lower() for w in result.warnings)
