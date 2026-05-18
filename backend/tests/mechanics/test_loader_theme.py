"""Tests for §3: theme CSS loading."""

from __future__ import annotations

from pathlib import Path

from grimoire.mechanics.discovery import discover
from grimoire.mechanics.loader import load_module

from .conftest import write_module


def _discover_one(root: Path, module_id: str):
    found, _ = discover([root])
    return next(d for d in found if d.module_dir.name == module_id)


def test_loader_reads_theme_css(mechanics_root: Path) -> None:
    write_module(
        mechanics_root,
        "themed",
        manifest={"ui": {"theme_css": "theme.css"}},
        theme_css=".sheet { color: red; }",
    )
    result = load_module(_discover_one(mechanics_root, "themed"))
    assert result.ok, result.errors
    assert result.theme_css == ".sheet { color: red; }"


def test_loader_no_theme_when_not_declared(mechanics_root: Path) -> None:
    write_module(mechanics_root, "plain", manifest={"sheet_kinds": []})
    result = load_module(_discover_one(mechanics_root, "plain"))
    assert result.ok, result.errors
    assert result.theme_css is None
    assert result.warnings == []


def test_loader_warns_when_theme_declared_but_missing(mechanics_root: Path) -> None:
    write_module(
        mechanics_root,
        "broken-theme",
        manifest={"ui": {"theme_css": "missing.css"}},
    )
    result = load_module(_discover_one(mechanics_root, "broken-theme"))
    assert result.ok, result.errors
    assert result.theme_css is None
    assert any("theme_css" in w and "missing" in w for w in result.warnings)


def test_loader_rejects_escaping_theme_path(mechanics_root: Path) -> None:
    write_module(
        mechanics_root,
        "escape",
        manifest={"ui": {"theme_css": "../escape.css"}},
    )
    result = load_module(_discover_one(mechanics_root, "escape"))
    assert result.ok, result.errors
    assert result.theme_css is None
    assert any("escape" in w.lower() for w in result.warnings)
