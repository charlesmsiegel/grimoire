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

import os
import pathlib
import stat
from pathlib import Path

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


def test_publish_cannot_replace_a_world_that_was_just_created(monkeypatch, tmp_path):
    """The P1 this seam used to carry. POSIX `rename` REPLACES an empty
    destination directory, and `create_world` used to `mkdir` a world and only
    then write its `world.md` -- so a fork or an import that had just seen the
    id free could rename its finished tree over that directory, and the creator
    would write its metadata into somebody else's copied world.

    `create_world` now publishes through this same seam, so a world directory
    is never empty and visible. `rename` refuses a non-empty destination, which
    turns the race into a lost id that `publish` already knows how to retry.
    """
    _home(monkeypatch, tmp_path)
    # 1. The invariant: creating a world goes through this seam, so there is no
    #    moment at which `worlds/<id>/` exists and holds nothing. The race
    #    itself lives in the microseconds between `dest.exists()` and the
    #    rename and cannot be reproduced; what CAN be pinned is that nothing
    #    puts an empty world directory there for the rename to land on.
    seen: list[Path] = []
    real = staging.publish
    # A scoped context, NOT `monkeypatch.undo()`: undo unwinds every patch this
    # test has made, `_home`'s `GRIMOIRE_HOME` included, and the rest of the
    # test would then run against the developer's real store.
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(staging, "publish",
                   lambda tree, base, cur: seen.append(tree) or real(tree, base, cur))
        wid = worlds.create_world("Saltmarch")
    assert seen, "create_world published its directory in place"
    assert (worlds.world_root(wid) / "world.md").is_file()

    # 2. The property that then makes the race harmless: `rename` refuses a
    #    NON-empty destination, so a publisher aimed at a live world loses the
    #    id rather than replacing it, and retries.
    with staging.staging_tree() as tree:
        (tree / "world.md").write_text("---\nname: Winifred\n---\n", encoding="utf-8")
        (tree / "marker.md").write_text("the copy", encoding="utf-8")
        got = staging.publish(tree, "saltmarch", wid)     # aimed straight at it

    assert got != wid, "publish took an id that was already a world"
    assert worlds.read_world(wid)["meta"]["name"] == "Saltmarch"
    assert not (worlds.world_root(wid) / "marker.md").exists()
    assert (worlds.world_root(got) / "marker.md").read_text(encoding="utf-8") == "the copy"


@pytest.mark.skipif(os.name == "nt", reason="POSIX mode bits")
def test_a_failure_leaves_nothing_even_behind_a_read_only_directory(
        monkeypatch, tmp_path):
    """The cleanup path's own version of the read-only problem.

    A copy applies the source's mode to each destination directory as it
    finishes it, so a copy that RAISES part-way returns here with `0555`
    directories already in place -- and `rmtree(ignore_errors=True)` cannot
    unlink out of one. Normalizing after a successful copy does not help here,
    because the successful copy is exactly what did not happen (Codex review).
    """
    _home(monkeypatch, tmp_path)
    # Asserted on the mode the tree has WHEN `rmtree` is reached, not on
    # whether the removal happened: root can unlink out of a `0555` directory,
    # so "it went away" is true for the wrong reason when the suite runs as
    # root -- the same trap the read-only *record* test sidesteps.
    seen: dict[str, int] = {}
    real_rmtree = staging.shutil.rmtree

    def spy(path, *a, **kw):
        sealed = pathlib.Path(path) / "world" / "lore"
        if sealed.is_dir():
            seen["mode"] = sealed.stat().st_mode
        return real_rmtree(path, *a, **kw)

    work = None
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(staging.shutil, "rmtree", spy)
        with pytest.raises(RuntimeError), staging.staging_tree() as tree:
            work = tree.parent
            sealed = tree / "lore"
            sealed.mkdir()
            (sealed / "a.md").write_text("half a record", encoding="utf-8")
            sealed.chmod(0o555)                    # as `copystat` would leave it
            raise RuntimeError("disk full")

    assert seen.get("mode"), "cleanup never reached the staged tree"
    assert seen["mode"] & stat.S_IWUSR, oct(seen["mode"])
    assert work is not None and not work.exists()


def test_two_staging_trees_do_not_collide(monkeypatch, tmp_path):
    """Nested rather than sequential: a fork and an import can be in flight at
    once, and one finishing must not remove the other's tree."""
    _home(monkeypatch, tmp_path)
    with staging.staging_tree() as first:
        (first / "world.md").write_text("---\nname: Mara\n---\n", encoding="utf-8")
        with staging.staging_tree() as second:
            assert first.parent != second.parent
            (second / "world.md").write_text("---\nname: Winifred\n---\n", encoding="utf-8")
        assert not second.parent.exists()
        assert "Mara" in (first / "world.md").read_text(encoding="utf-8")
