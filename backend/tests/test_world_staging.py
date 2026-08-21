"""The seam both whole-world writers go through (`store/worlds/staging.py`).

`repoint_urls` and `publish` are exercised end-to-end by the two suites that
use them — `test_world_bundle.py` and `test_world_fork.py`. `staging_tree` is
here on its own because of what it replaced: the import and the fork each kept
their work directory by hand, and one of them got it wrong in a way that
deleted a directory outside the store entirely. A seam that exists to make a
mistake unrepeatable is worth pinning directly rather than only through its
callers.
"""

from __future__ import annotations

import pytest

from grimoire.store import worlds
from grimoire.store.worlds import staging


def _home(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))


def test_staging_tree_yields_an_empty_directory_and_removes_it(monkeypatch, tmp_path):
    _home(monkeypatch, tmp_path)
    with staging.staging_tree() as tree:
        assert tree.is_dir() and not any(tree.iterdir())
        assert staging.staging_root() in tree.parents
        work = tree.parent
        (tree / "world.md").write_text("---\nname: X\n---\n", encoding="utf-8")
    assert not work.exists()


def test_staging_tree_removes_the_work_directory_after_a_failure(monkeypatch, tmp_path):
    _home(monkeypatch, tmp_path)
    work = None
    with pytest.raises(RuntimeError), staging.staging_tree() as tree:
        work = tree.parent
        (tree / "half.md").write_text("partial", encoding="utf-8")
        raise RuntimeError("disk full")
    assert work is not None and not work.exists()


def test_staging_tree_removes_the_parent_after_publish_moved_the_child(monkeypatch, tmp_path):
    """The success shape: `publish` renames the yielded directory into the
    library, so what is left to clean up is an empty parent — and the cleanup
    must not follow the tree it no longer owns."""
    _home(monkeypatch, tmp_path)
    with staging.staging_tree() as tree:
        (tree / "world.md").write_text("---\nname: Realm\n---\n", encoding="utf-8")
        wid = staging.publish(tree, "realm", "realm")
        work = tree.parent
    assert wid == "realm"
    assert not work.exists()
    assert worlds.world_meta_path(wid).is_file()
    assert [w["id"] for w in worlds.list_worlds()] == ["realm"]


def test_two_staging_trees_do_not_collide(monkeypatch, tmp_path):
    """Nested rather than sequential: a fork and an import can be in flight at
    once, and one finishing must not remove the other's tree."""
    _home(monkeypatch, tmp_path)
    with staging.staging_tree() as first:
        (first / "world.md").write_text("---\nname: Mine\n---\n", encoding="utf-8")
        with staging.staging_tree() as second:
            assert first.parent != second.parent
            (second / "world.md").write_text("---\nname: Theirs\n---\n", encoding="utf-8")
        assert not second.parent.exists()
        assert "Mine" in (first / "world.md").read_text(encoding="utf-8")
