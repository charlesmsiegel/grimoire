from __future__ import annotations

from pathlib import Path

from grimoire.files import atomic_write_text


def test_atomic_write_text_creates_parents_and_writes(tmp_path: Path) -> None:
    target = tmp_path / "nested" / "dir" / "file.md"
    atomic_write_text(target, "hello")
    assert target.read_text(encoding="utf-8") == "hello"


def test_atomic_write_text_replaces_existing_without_temp_residue(tmp_path: Path) -> None:
    target = tmp_path / "file.yaml"
    target.write_text("old", encoding="utf-8")
    atomic_write_text(target, "new")
    assert target.read_text(encoding="utf-8") == "new"
    assert [p.name for p in tmp_path.iterdir()] == ["file.yaml"]
