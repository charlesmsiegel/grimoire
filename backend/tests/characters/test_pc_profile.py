"""Tests for PC profile path helpers, I/O, and rendering."""

from __future__ import annotations

from pathlib import Path

from grimoire.state_store.paths import pc_profile_path, pc_profile_revisions_dir


def test_pc_profile_path(tmp_path: Path) -> None:
    result = pc_profile_path(tmp_path, "camp-1", "alistair")
    assert result == tmp_path / "campaigns" / "camp-1" / "characters" / "alistair" / "profile.md"


def test_pc_profile_revisions_dir(tmp_path: Path) -> None:
    result = pc_profile_revisions_dir(tmp_path, "camp-1", "alistair")
    assert result == tmp_path / "campaigns" / "camp-1" / "characters" / "alistair" / "revisions"
