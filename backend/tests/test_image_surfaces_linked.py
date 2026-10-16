"""`image_surfaces.linked` answers True when it cannot inspect a component.

Callers skip a linked directory and never unlink through one. A component the
inventory cannot lstat (permission denied) used to raise out of the whole
inventory pass, because the URL-subject walk asks `linked` outside any guard.
"""

from __future__ import annotations

from pathlib import Path

from grimoire.store import image_migration, image_refs, image_surfaces


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


def test_linked_slot_asks_about_the_placement_folder_and_file(tmp_path, monkeypatch):
    """A junctioned `image-refs/` folder is a link the migration must refuse,
    and `linked` is the one check that knows about junctions, so the placement
    folder and file go through it rather than through `is_symlink()` (#478)."""
    root = tmp_path / "root"
    d = root / "worlds" / "w" / "images"
    d.mkdir(parents=True)
    asked: list[Path] = []
    real = image_surfaces.linked

    def recording(r, p):
        asked.append(p)
        return real(r, p)

    monkeypatch.setattr(image_surfaces, "linked", recording)
    image_migration._linked_slot(root, d, "portrait")
    assert d / image_refs.REFS_DIR in asked
    assert image_refs.ref_path(d, "portrait") in asked

    junctioned = d / image_refs.REFS_DIR
    monkeypatch.setattr(image_surfaces, "linked",
                        lambda r, p: p == junctioned or real(r, p))
    assert image_migration._linked_slot(root, d, "portrait") is True
