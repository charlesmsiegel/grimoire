"""`image_surfaces.linked` answers True when it cannot inspect a component.

Callers skip a linked directory and never unlink through one. A component the
inventory cannot lstat (permission denied) used to raise out of the whole
inventory pass, because the URL-subject walk asks `linked` outside any guard.
"""

from __future__ import annotations

from pathlib import Path

from grimoire.store import image_surfaces


def test_linked_fails_closed_when_a_component_cannot_be_inspected(tmp_path, monkeypatch):
    root = tmp_path / "root"
    d = root / "worlds" / "w"
    d.mkdir(parents=True)
    denied = root / "worlds"
    real_lstat = Path.lstat

    def lstat(self, *args, **kwargs):
        if self == denied:
            raise PermissionError(13, "Permission denied", str(self))
        return real_lstat(self, *args, **kwargs)

    monkeypatch.setattr(Path, "lstat", lstat)
    assert image_surfaces.linked(root, d) is True


def test_linked_still_answers_false_for_a_missing_component(tmp_path):
    root = tmp_path / "root"
    d = root / "worlds" / "not-yet"
    root.mkdir()
    assert image_surfaces.linked(root, d) is False
