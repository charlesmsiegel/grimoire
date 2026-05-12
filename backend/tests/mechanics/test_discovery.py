"""Tests for mechanics module discovery."""

from __future__ import annotations

from pathlib import Path

from grimoire.mechanics.discovery import discover

from .conftest import write_module


def test_discover_finds_a_module_dir(mechanics_root: Path) -> None:
    write_module(mechanics_root, "wod-mechanics")
    found, errors = discover([mechanics_root])
    assert errors == []
    assert len(found) == 1
    assert found[0].module_dir.name == "wod-mechanics"
    assert found[0].entry_path.name == "mechanics.py"
    assert found[0].raw_manifest["id"] == "wod-mechanics"


def test_discover_ignores_dirs_without_manifest(mechanics_root: Path) -> None:
    (mechanics_root / "not-a-module").mkdir()
    found, errors = discover([mechanics_root])
    assert found == []
    assert errors == []


def test_discover_reports_bad_yaml(mechanics_root: Path) -> None:
    bad = mechanics_root / "broken"
    bad.mkdir()
    (bad / "manifest.yaml").write_text("key: [unclosed\n", encoding="utf-8")
    found, errors = discover([mechanics_root])
    assert found == []
    assert len(errors) == 1
    assert errors[0].module_dir.name == "broken"


def test_discover_rejects_non_mapping_manifest(mechanics_root: Path) -> None:
    weird = mechanics_root / "weird"
    weird.mkdir()
    (weird / "manifest.yaml").write_text("- just\n- a\n- list\n", encoding="utf-8")
    found, errors = discover([mechanics_root])
    assert found == []
    assert "mapping" in errors[0].message


def test_discover_dedupes_ids_across_roots(tmp_path: Path) -> None:
    root_a = tmp_path / "a"
    root_b = tmp_path / "b"
    root_a.mkdir()
    root_b.mkdir()
    write_module(root_a, "shared")
    write_module(root_b, "shared")
    found, errors = discover([root_a, root_b])
    assert len(found) == 1
    assert found[0].source_root == root_a
    assert len(errors) == 1
    assert "duplicate" in errors[0].message


def test_discover_skips_missing_root(tmp_path: Path) -> None:
    missing = tmp_path / "nope"
    found, errors = discover([missing])
    assert found == []
    assert errors == []
