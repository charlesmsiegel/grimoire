"""A V2 library's own ids never make it look like V1, and a refused commit leaves no empty folders.

The V1 fingerprint (a ``worlds``/``campaigns`` folder beside a V1-only root entry) still guards the
library root, everything above it and any folder a mount gives a second name outside the root;
it skips only what lies strictly below the opened root under every name it has (a V1 library
mounted into the tree: test_store_isolation.py).
"""

from __future__ import annotations


import pytest

from grimoire.core.identity import InstanceRef
from grimoire.library import V1IsolationError
from grimoire.store import LibraryStore, ProcessLocker
from grimoire.testing import Interleave, RootedFileOps, Target

from store_helpers import WREN_R1, instance, new_library, owner_handle, seed


def _put(store, ref, hp=3):
    txn = store.transaction()
    txn.put(instance(ref, WREN_R1, hp=hp), None)
    return txn.commit(owner_handle())


@pytest.mark.parametrize("first, second", [("worlds", "llm_connections"), ("campaigns", "regex.json"),
                                           ("llm_connections", "worlds")])
def test_campaign_ids_spelling_v1_markers_never_lock_the_library(tmp_path, first, second):
    root = tmp_path / "lib"
    with new_library(root) as store:
        seed(store)
        _put(store, InstanceRef(first, "character", "a"))
        _put(store, InstanceRef(second, "character", "b"))
        _put(store, InstanceRef("c9", "character", "z"))
    with LibraryStore.open(root) as store:  # a fresh session, an unrelated campaign
        _put(store, InstanceRef("c10", "character", "y"))
        _put(store, InstanceRef(first, "character", "a2"))
        view = store.load()
        assert view.problems == ()
        assert {ref.campaign_id for ref in view.instances} >= {first, second, "c9", "c10"}


def test_the_root_itself_keeps_the_fingerprint(tmp_path):
    root = tmp_path / "lib"
    with new_library(root) as store:
        seed(store)
        (root / "regex.json").write_text("[]")  # beside the root's own campaigns/: V1's shape
        with pytest.raises(V1IsolationError):
            _put(store, InstanceRef("c1", "character", "x"))
    with pytest.raises(V1IsolationError):
        LibraryStore.open(root)


def test_a_refused_commit_removes_only_the_empty_folders_it_created(tmp_path):
    locker = ProcessLocker()
    root = tmp_path / "root"
    root.mkdir()
    with new_library(root / "lib", locker=locker) as store:
        seed(store)
        _put(store, InstanceRef("c1", "character", "kept"))
    keep = root / "lib" / "campaigns" / "c1" / "instances" / "character"

    def v1_appears(ops, r):  # after staging made the folders: the root suddenly has V1's shape
        ops.write_bytes(r / "lib" / "regex.json", b"[]")
        ops.make_dirs(r / "lib" / "worlds")

    ops = RootedFileOps(root, plan=[Interleave(Target("make_temp", path="lib/.txn/rec*"), v1_appears)], record=False)
    with LibraryStore.open(root / "lib", ops=ops, locker=locker) as store:
        txn = store.transaction()
        txn.put(instance(InstanceRef("c9", "character", "x"), WREN_R1), None)
        txn.put(instance(InstanceRef("c1", "character", "new"), WREN_R1), None)
        with pytest.raises(V1IsolationError):
            txn.commit(owner_handle())
    assert not (root / "lib" / "campaigns" / "c9").exists()  # created by this commit, still empty: gone
    assert keep.is_dir() and (keep / "kept.json").is_file()  # existed before: untouched
    assert not any(name.endswith(".new") for name in (p.name for p in (root / "lib" / ".txn").iterdir()))
