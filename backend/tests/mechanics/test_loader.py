"""Tests for the mechanics module loader."""

from __future__ import annotations

import textwrap
from pathlib import Path

from grimoire.mechanics.discovery import discover
from grimoire.mechanics.loader import load_module, satisfies_mechanics_protocol

from .conftest import default_mechanics_py, write_module


def _discover_one(root: Path, module_id: str):
    found, _ = discover([root])
    return next(d for d in found if d.module_dir.name == module_id)


def test_load_returns_ok_for_valid_module(mechanics_root: Path) -> None:
    write_module(mechanics_root, "wod-mechanics")
    result = load_module(_discover_one(mechanics_root, "wod-mechanics"))
    assert result.ok, result.errors
    assert result.manifest is not None
    assert result.manifest.id == "wod-mechanics"
    assert result.instance is not None
    assert result.instance.id == "wod-mechanics"
    assert satisfies_mechanics_protocol(result.instance)


def test_loader_rejects_id_mismatch(mechanics_root: Path) -> None:
    write_module(
        mechanics_root,
        "wod-mechanics",
        manifest={"id": "wod-mechanics", "name": "WoD"},
        mechanics_py=default_mechanics_py("something-else"),
    )
    result = load_module(_discover_one(mechanics_root, "wod-mechanics"))
    assert not result.ok
    joined = "; ".join(result.errors)
    assert "instance.id" in joined and "manifest.id" in joined


def test_loader_rejects_dir_name_mismatch(mechanics_root: Path) -> None:
    write_module(
        mechanics_root,
        "actual-dir",
        manifest={"id": "different-id"},
    )
    result = load_module(_discover_one(mechanics_root, "actual-dir"))
    assert not result.ok
    assert any("directory name" in e for e in result.errors)


def test_loader_reports_missing_entry_class(mechanics_root: Path) -> None:
    write_module(
        mechanics_root,
        "empty",
        mechanics_py="x = 1\n",
    )
    result = load_module(_discover_one(mechanics_root, "empty"))
    assert not result.ok
    assert any("must define" in e for e in result.errors)


def test_loader_reports_missing_mechanics_py(mechanics_root: Path) -> None:
    write_module(mechanics_root, "no-entry", omit_mechanics_py=True)
    result = load_module(_discover_one(mechanics_root, "no-entry"))
    assert not result.ok
    assert any("missing mechanics.py" in e for e in result.errors)


def test_loader_reports_protocol_gap(mechanics_root: Path) -> None:
    incomplete = textwrap.dedent(
        """
        class Mechanics:
            id = "incomplete"
            name = "Incomplete"
            version = "1.0.0"
            api_version = "1"

            def sheet_schema(self, entity_kind):
                return None
        """
    ).strip()
    write_module(mechanics_root, "incomplete", mechanics_py=incomplete)
    result = load_module(_discover_one(mechanics_root, "incomplete"))
    assert not result.ok
    joined = "; ".join(result.errors)
    assert "missing members" in joined
    assert "resolve_roll" in joined


def test_loader_accepts_pre_built_MECHANICS_instance(mechanics_root: Path) -> None:
    src = default_mechanics_py("ready").replace(
        "class Mechanics:",
        "class _Mechanics:",
    )
    src += "\n\nMECHANICS = _Mechanics()\n"
    write_module(mechanics_root, "ready", mechanics_py=src)
    result = load_module(_discover_one(mechanics_root, "ready"))
    assert result.ok, result.errors


def test_loader_honours_entry_class_in_manifest(mechanics_root: Path) -> None:
    src = default_mechanics_py("custom-entry").replace(
        "class Mechanics:",
        "class MySystem:",
    )
    write_module(
        mechanics_root,
        "custom-entry",
        manifest={"entry_class": "MySystem"},
        mechanics_py=src,
    )
    result = load_module(_discover_one(mechanics_root, "custom-entry"))
    assert result.ok, result.errors


def test_loader_reports_unsupported_api_version(mechanics_root: Path) -> None:
    write_module(mechanics_root, "future", manifest={"api_version": "99"})
    result = load_module(_discover_one(mechanics_root, "future"))
    assert not result.ok
    assert any("api_version" in e for e in result.errors)


def test_loader_reports_import_error(mechanics_root: Path) -> None:
    write_module(mechanics_root, "boom", mechanics_py="raise RuntimeError('kaboom')\n")
    result = load_module(_discover_one(mechanics_root, "boom"))
    assert not result.ok
    assert any("failed to import" in e for e in result.errors)
