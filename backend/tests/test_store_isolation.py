"""The store never writes into or through a V1 library, a link, or a swapped root (F1, F2, N1)."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from grimoire.core.identity import InstanceRef
from grimoire.library import LibraryPathError, V1IsolationError
from grimoire.store import CommitIncomplete, LibraryStore, LinkRefused, ProcessLocker, RecoveryFailed, WriteConflict
from grimoire.testing import Crash, RootedFileOps, Target
from grimoire.testing.fileops import snapshot

from store_helpers import HARBOR, WREN_A, WREN_R1, harbor_manifest, instance, make_dir_link, owner_handle, seed, wren


def _v1_library(path: Path) -> Path:
    """A synthetic directory that fingerprints as a V1 (0.2.1) library."""
    (path / "campaigns" / "c1" / "instances" / "character").mkdir(parents=True)
    (path / "worlds").mkdir()
    (path / "regex.json").write_text("[]")
    return path


def test_a_library_inside_v1_is_refused(tmp_path):
    v1 = _v1_library(tmp_path / "old")
    before = snapshot(v1)
    with pytest.raises(V1IsolationError):
        LibraryStore.create(v1 / "v2-here")
    with pytest.raises(V1IsolationError):
        LibraryStore.open(v1)
    assert snapshot(v1) == before


def test_a_link_into_v1_is_never_written_through(tmp_path):
    v1 = _v1_library(tmp_path / "old")
    before = snapshot(v1)
    store = LibraryStore.create(tmp_path / "lib")
    make_dir_link(tmp_path / "lib" / "campaigns", v1 / "campaigns")
    txn = store.transaction()
    txn.put(instance(WREN_A, WREN_R1), None)
    with pytest.raises(LibraryPathError):  # LinkRefused, or V1IsolationError where the link resolves
        txn.commit(owner_handle())
    store.close()
    assert snapshot(v1) == before


def test_a_link_swapped_in_while_down_is_never_recovered_through(tmp_path):
    v1 = _v1_library(tmp_path / "old")
    root = tmp_path / "root"
    root.mkdir()
    locker = ProcessLocker()
    with LibraryStore.create(root / "lib", locker=locker) as store:
        seed(store)
    ops = RootedFileOps(root, plan=[Crash(Target("replace", target="lib/.txn/journal.json"), "after")],
                        record=False)
    with pytest.raises(BaseException):
        with LibraryStore.open(root / "lib", ops=ops, locker=locker) as store:
            txn = store.transaction()
            txn.put(instance(WREN_A, WREN_R1, hp=1), store.read(WREN_A).etag)
            txn.commit(owner_handle())
    before = snapshot(v1)
    os.rename(root / "lib" / "campaigns", root / "campaigns-moved")
    make_dir_link(root / "lib" / "campaigns", v1 / "campaigns")
    journal = (root / "lib" / ".txn" / "journal.json").read_bytes()
    with pytest.raises(RecoveryFailed):
        LibraryStore.open(root / "lib", locker=locker)
    assert snapshot(v1) == before
    assert (root / "lib" / ".txn" / "journal.json").read_bytes() == journal  # nothing changed
    assert locker.held == set()


def test_a_conflicts_link_refuses_before_authorization(tmp_path):
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    with LibraryStore.create(tmp_path / "lib") as store:
        seed(store)
        make_dir_link(tmp_path / "lib" / ".conflicts", elsewhere)
        authority_handle = owner_handle()
        txn = store.transaction()
        txn.put(instance(InstanceRef("c3", "character", "x"), WREN_R1), None)
        with pytest.raises(LinkRefused):
            txn.commit(authority_handle)
    assert list(elsewhere.iterdir()) == []


class _SwapConflictsOnAbort:
    """Real disk operations that swap ``.conflicts`` for a link just as a commit aborts."""

    def __init__(self, root: Path, elsewhere: Path) -> None:
        from grimoire.store import DiskOps
        self._disk, self._root, self._elsewhere, self._writes = DiskOps(), root, elsewhere, 0

    def __getattr__(self, name):
        return getattr(self._disk, name)

    def read_bytes(self, path):
        data = self._disk.read_bytes(path)
        if Path(path).name == "manifest.json" and (self._root / ".txn" / "journal.json").exists():
            (Path(path)).write_bytes(b'{"edited": true}')  # a racing human edit: forces an abort
            if not (self._root / ".conflicts").exists():
                make_dir_link(self._root / ".conflicts", self._elsewhere)
        return data


def test_a_conflicts_link_swapped_in_mid_commit_is_refused(tmp_path):
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    lib = tmp_path / "lib"
    with LibraryStore.create(lib) as store:
        seed(store)
        source = store.read_source(HARBOR)
    with LibraryStore.open(lib, ops=_SwapConflictsOnAbort(lib, elsewhere)) as store:
        txn = store.transaction()
        txn.put(wren("r2"), None)
        txn.put(harbor_manifest({"character/wren": "r2"}), source.etag)
        with pytest.raises((CommitIncomplete, WriteConflict)) as raised:
            txn.commit(owner_handle())
    assert isinstance(raised.value, CommitIncomplete)
    assert list(elsewhere.iterdir()) == []


def test_a_root_swapped_after_open_refuses_to_commit(tmp_path):
    locker = ProcessLocker()  # Windows cannot rename a directory whose lock file is open
    store = LibraryStore.create(tmp_path / "lib", locker=locker)
    seed(store)
    os.rename(tmp_path / "lib", tmp_path / "lib-original")
    (tmp_path / "lib").mkdir()
    txn = store.transaction()
    txn.put(instance(InstanceRef("c3", "character", "x"), WREN_R1), None)
    with pytest.raises(LibraryPathError):
        txn.commit(owner_handle())
    store.close()
    assert list((tmp_path / "lib").iterdir()) == []


def test_a_linked_staging_directory_is_refused_everywhere(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "precious.new").write_text("not the store's")
    (outside / "journal.json").write_text("{}")
    lib = tmp_path / "lib"
    store = LibraryStore.create(lib, locker=ProcessLocker())
    seed(store)
    os.rename(lib / ".txn", tmp_path / "txn-moved")
    make_dir_link(lib / ".txn", outside)
    before = snapshot(outside)
    txn = store.transaction()
    txn.put(instance(InstanceRef("c3", "character", "x"), WREN_R1), None)
    with pytest.raises(LinkRefused):
        txn.commit(owner_handle())
    store.close()
    with pytest.raises(LinkRefused):
        LibraryStore.open(lib, locker=ProcessLocker())
    with pytest.raises(LinkRefused):
        LibraryStore.discard_journal(lib, locker=ProcessLocker())
    assert snapshot(outside) == before


def test_a_v1_shaped_directory_inside_the_library_is_never_written(tmp_path):
    with LibraryStore.create(tmp_path / "lib") as store:
        seed(store)
        v1 = _v1_library(tmp_path / "lib" / "campaigns" / "c7")
        before = snapshot(v1)
        txn = store.transaction()
        txn.put(instance(InstanceRef("c7", "character", "x"), WREN_R1), None)
        with pytest.raises(V1IsolationError):
            txn.commit(owner_handle())
    assert snapshot(v1) == before


def test_the_executor_writes_only_the_resource_it_was_authorized_for(tmp_path):
    from grimoire.security import Decision, Reason, ResourcePath
    from grimoire.store import WriteDenied

    class Elsewhere:
        def authorize(self, permission, resource, payload=None, approval_id=None):
            return Decision(True, Reason.GRANTED, ResourcePath.parse("campaigns/c1/instances/character/other.json"))

    with LibraryStore.create(tmp_path / "lib") as store:
        txn = store.transaction()
        txn.put(instance(InstanceRef("c3", "character", "x"), WREN_R1), None)
        with pytest.raises(WriteDenied):
            txn.commit(Elsewhere())
    assert not (tmp_path / "lib" / "campaigns").exists()


def test_staging_swapped_for_a_link_during_authorization_is_never_written(tmp_path):
    v1 = _v1_library(tmp_path / "old")
    (v1 / "precious.new").write_text("not the store's")
    lib = tmp_path / "lib"
    store = LibraryStore.create(lib, locker=ProcessLocker())
    seed(store)
    before = snapshot(v1)
    real = owner_handle()

    class SwapsStaging:  # slow approval, during which `.txn` becomes a link into V1
        def authorize(self, *args, **kwargs):
            if not (lib / ".txn").is_symlink():
                os.rename(lib / ".txn", tmp_path / "txn-moved")
                make_dir_link(lib / ".txn", v1)
            return real.authorize(*args, **kwargs)

    txn = store.transaction()
    txn.put(instance(InstanceRef("c3", "character", "x"), WREN_R1), None)
    with pytest.raises(LinkRefused):
        txn.commit(SwapsStaging())
    store.close()
    assert snapshot(v1) == before
