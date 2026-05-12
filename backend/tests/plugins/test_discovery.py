"""Tests for plugin filesystem discovery."""

from __future__ import annotations

from pathlib import Path

from grimoire.plugins.discovery import discover

from .conftest import write_plugin


def test_discover_finds_manifest_directories(plugins_root: Path) -> None:
    write_plugin(plugins_root, "alpha")
    write_plugin(plugins_root, "beta")
    discovered, errors = discover([plugins_root])
    ids = sorted(d.raw_manifest["id"] for d in discovered)
    assert ids == ["alpha", "beta"]
    assert errors == []


def test_discover_skips_directories_without_manifest(plugins_root: Path) -> None:
    (plugins_root / "not-a-plugin").mkdir()
    write_plugin(plugins_root, "real")
    discovered, _ = discover([plugins_root])
    assert [d.raw_manifest["id"] for d in discovered] == ["real"]


def test_discover_skips_hidden_directories(plugins_root: Path) -> None:
    write_plugin(plugins_root, "alpha")
    # Mimic a `.venvs` cache directory.
    (plugins_root / ".venvs").mkdir()
    discovered, _ = discover([plugins_root])
    assert [d.raw_manifest["id"] for d in discovered] == ["alpha"]


def test_discover_returns_yaml_errors(plugins_root: Path) -> None:
    plugin_dir = plugins_root / "broken"
    plugin_dir.mkdir()
    (plugin_dir / "manifest.yaml").write_text(": bad : yaml", encoding="utf-8")
    discovered, errors = discover([plugins_root])
    assert discovered == []
    assert len(errors) == 1
    assert errors[0].plugin_dir == plugin_dir


def test_discover_rejects_non_mapping_manifest(plugins_root: Path) -> None:
    plugin_dir = plugins_root / "weird"
    plugin_dir.mkdir()
    (plugin_dir / "manifest.yaml").write_text("- not\n- a\n- mapping\n", encoding="utf-8")
    discovered, errors = discover([plugins_root])
    assert discovered == []
    assert len(errors) == 1
    assert "mapping" in errors[0].message


def test_discover_missing_root_is_silent(tmp_path: Path) -> None:
    discovered, errors = discover([tmp_path / "does-not-exist"])
    assert discovered == []
    assert errors == []


def test_discover_flags_duplicate_ids(plugins_root: Path, tmp_path: Path) -> None:
    other_root = tmp_path / "other_plugins"
    other_root.mkdir()
    write_plugin(plugins_root, "twin")
    write_plugin(other_root, "twin")
    discovered, errors = discover([plugins_root, other_root])
    assert len(discovered) == 1
    assert any("duplicate" in e.message for e in errors)


def test_discover_bundled_root_scanned_first(plugins_root: Path, tmp_path: Path) -> None:
    bundled_root = tmp_path / "bundled"
    bundled_root.mkdir()
    write_plugin(bundled_root, "alpha", manifest={"name": "Bundled"})
    write_plugin(plugins_root, "beta")
    discovered, _ = discover([plugins_root], bundled_roots=[bundled_root])
    ids = [d.raw_manifest["id"] for d in discovered]
    assert ids == ["alpha", "beta"]
    bundled = next(d for d in discovered if d.raw_manifest["id"] == "alpha")
    assert bundled.bundled is True
