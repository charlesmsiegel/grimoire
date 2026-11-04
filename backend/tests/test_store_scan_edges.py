"""Scan and reference-check edges from brutal-review round 13 (019): entries that cannot be inspected,
noise-named links, stray files, look-alike reads, deep trees, and a replace whose hold vanished."""

from __future__ import annotations

import os
import sys

import pytest

import grimoire.store.engine as engine
import grimoire.store.library as library
from grimoire.core.identity import InstanceRef
from grimoire.store import (
    CommitIncomplete, LibraryStore, NameCollision, RevisionReferenced, SourceManifest, WriteConflict, etag,
)
from grimoire.store.envelope import record_document
from grimoire.store.journal import Entry, Journal
from grimoire.store.jsonfmt import render
from grimoire.sync import identify, sync

from store_helpers import HARBOR, OWNER, WREN_A, WREN_R1, harbor_manifest, instance, make_dir_link, owner_handle, seed, wren
from sync_helpers import handle, handles, make_pair

WREN_A_PATH = "campaigns/c1/instances/character/wren-a.json"
R2 = "sources/world/harbor/_source/records/character/wren/r2.json"


@pytest.fixture
def lib(tmp_path):
    store = LibraryStore.create(tmp_path / "lib")
    seed(store)
    yield store
    store.close()


def _diverged_r2(tmp_path):
    """Two libraries whose r2 differs and that pin only r1: r2 can be replaced on ``b``."""
    a, b = (LibraryStore.create(tmp_path / name) for name in ("a", "b"))
    for store, bio in ((a, "captain"), (b, "pirate")):
        seed(store)
        txn = store.transaction()
        txn.put(wren("r2", bio), None)
        txn.put(harbor_manifest({"character/wren": "r1"}), store.read_source(HARBOR).etag)
        txn.commit(owner_handle())
    return a, b


def _replace(a, b):
    chosen, replaced = (a.root / R2).read_bytes(), (b.root / R2).read_bytes()
    txn = b.transaction()
    txn.put_exact(R2, chosen, etag(replaced), replace_unreferenced=True)
    txn.keep_version(R2, replaced)
    return txn, chosen, replaced


def test_an_entry_listed_but_not_inspectable_is_blocked_not_a_crash(lib, monkeypatch):
    """A folder with read but no search permission lists, but each child's lstat fails: that child is
    `unreadable`, as load() copes with it, and one folder never stops every sync."""
    real, target = os.lstat, str(lib.root / WREN_A_PATH)

    def lstat(path, *args, **kwargs):
        if os.fspath(path) == target:
            raise PermissionError(13, "Permission denied", target)
        return real(path, *args, **kwargs)
    monkeypatch.setattr(os, "lstat", lstat)
    lib.load()
    assert lib.scan().blocked_reason(WREN_A_PATH) == "unreadable"


@pytest.mark.skipif(os.name == "nt", reason="needs dir_fd to build a tree deeper than the path limit")
def test_a_folder_chain_deeper_than_any_record_is_skipped_not_a_recursion_crash(lib):
    fd = os.open(lib.root / "sources", os.O_RDONLY)
    try:
        for _ in range(sys.getrecursionlimit() + 50):
            os.mkdir("d", dir_fd=fd)
            deeper = os.open("d", os.O_RDONLY, dir_fd=fd)
            os.close(fd)
            fd = deeper
    finally:
        os.close(fd)
    found = lib.scan()
    assert WREN_A_PATH in found.files and not [path for path in found.blocked if path.startswith("sources/d")]


def test_a_noise_named_link_is_blocked_as_a_link(lib, tmp_path):
    """`notes.swp` is a valid source id: a link there is never skipped as noise."""
    outside = tmp_path / "outside"
    (outside / "_source").mkdir(parents=True)
    make_dir_link(lib.root / "sources" / "world" / "notes.swp", outside)
    assert lib.scan().blocked_reason("sources/world/notes.swp/_source/manifest.json") == "link"


def test_a_noise_named_link_counts_for_the_reference_check(tmp_path):
    a, b = _diverged_r2(tmp_path)
    try:
        (tmp_path / "outside").mkdir()
        make_dir_link(b.root / "sources" / "world" / "notes.swp", tmp_path / "outside")
        txn, _, _ = _replace(a, b)
        with pytest.raises(RevisionReferenced):
            txn.commit(owner_handle("sources", "campaigns", "kept"))
    finally:
        a.close()
        b.close()


def test_a_noise_named_link_is_a_blocked_conflict_and_the_rest_still_syncs(tmp_path):
    desktop, phone = make_pair(tmp_path)
    try:
        txn = desktop.transaction()
        txn.put(SourceManifest("world:notes.swp", OWNER, "N", {}, ()), None)
        txn.commit(handle())
        (tmp_path / "outside" / "_source").mkdir(parents=True)
        make_dir_link(phone.root / "sources" / "world" / "notes.swp", tmp_path / "outside")
        report = sync(desktop, phone, handles())
        assert report.conflict("sources/world/notes.swp/_source/manifest.json").code == "blocked"
    finally:
        desktop.close()
        phone.close()


@pytest.mark.parametrize("name", ["README.md", "Desktop.ini", "Notes.txt"])
def test_a_stray_capitalized_file_in_campaigns_never_blocks_a_revision_replace(tmp_path, name):
    """A regular file directly under campaigns/ is not a campaign folder on any filesystem."""
    a, b = _diverged_r2(tmp_path)
    try:
        (b.root / "campaigns" / name).write_bytes(b"note")
        txn, chosen, _ = _replace(a, b)
        txn.commit(owner_handle("sources", "campaigns", "kept"))
        assert (b.root / R2).read_bytes() == chosen
    finally:
        a.close()
        b.close()


def test_read_exact_under_a_lookalike_folder_is_a_collision_not_absent(lib):
    os.rename(lib.root / "campaigns" / "c1", lib.root / "campaigns" / "C1")
    with pytest.raises(NameCollision):
        lib.read_exact(WREN_A_PATH)


def test_sync_peers_under_a_lookalike_folder_is_a_collision_not_empty(lib):
    identify(lib, "desk")
    (lib.root / ".sync" / "Peers" / "phone").mkdir(parents=True)
    (lib.root / ".sync" / "Peers" / "phone" / "archive.json").write_bytes(b"{}")
    with pytest.raises(NameCollision):
        lib.sync_peers()


def test_a_replace_whose_hold_vanished_live_never_publishes(tmp_path, monkeypatch):
    """Another program removes the hold's staged file right after the commit point: the replace is
    abandoned whole, so the replaced bytes are never lost."""
    a, b = _diverged_r2(tmp_path)
    real = engine.settle

    def settle(access, journal, *, live=False):
        if live:
            for entry in journal.entries:
                if entry.kind == "hold":
                    access.ops.remove(access.path((".txn", entry.temp)))
        return real(access, journal, live=live)
    try:
        monkeypatch.setattr(library.engine, "settle", settle)
        txn, _, replaced = _replace(a, b)
        with pytest.raises((CommitIncomplete, WriteConflict)):
            txn.commit(owner_handle("sources", "campaigns", "kept"))
        monkeypatch.undo()
        assert (b.root / R2).read_bytes() == replaced
    finally:
        a.close()
        b.close()


def test_recovery_with_one_entry_published_never_shadows_a_lookalike_parent(tmp_path):
    """The recovery path past the pre-publish guard (one entry already published) still never creates
    a second folder beside a look-alike parent."""
    root = tmp_path / "lib"
    with LibraryStore.create(root) as store:
        seed(store)
    wren_a = tuple(WREN_A_PATH.split("/"))
    pup_parts = ("campaigns", "c3", "instances", "creature", "pup.json")
    old = root.joinpath(*wren_a).read_bytes()
    new_a = render(record_document(instance(WREN_A, WREN_R1, hp=7), etag(old)))
    root.joinpath(*wren_a).write_bytes(new_a)  # entry 1 was published before the crash
    pup = render(record_document(instance(InstanceRef("c3", "creature", "pup"), WREN_R1), None))
    (root / ".txn" / "recpup.new").write_bytes(pup)
    journal = Journal("commit", (Entry(wren_a, "reca.new", etag(old), etag(new_a), "publish"),
                                 Entry(pup_parts, "recpup.new", None, etag(pup), "publish")))
    (root / ".txn" / "journal.json").write_bytes(journal.render())
    (root / "campaigns" / "C3" / "instances" / "creature").mkdir(parents=True)  # renamed by case while down
    with LibraryStore.open(root) as store:
        report = store.last_recovery
    assert sorted(os.listdir(root / "campaigns")) == ["C3", "c1", "c2"]
    assert report.preserved and "/".join(pup_parts) not in dict(report.published)


# -- round 14 -------------------------------------------------------------------------------------

def _refusing_lstat(monkeypatch, target):
    real = os.lstat

    def lstat(path, *args, **kwargs):
        if os.fspath(path) == str(target):
            raise PermissionError(13, "Permission denied", str(target))
        return real(path, *args, **kwargs)
    monkeypatch.setattr(os, "lstat", lstat)


def test_an_uninspectable_entry_under_sources_means_cannot_prove_not_a_crash(tmp_path, monkeypatch):
    a, b = _diverged_r2(tmp_path)
    try:
        (b.root / "sources" / "world" / "stray").mkdir()
        _refusing_lstat(monkeypatch, b.root / "sources" / "world" / "stray")
        assert b.scan().blocked_reason("sources/world/stray") == "unreadable"
        txn, _, _ = _replace(a, b)
        with pytest.raises(RevisionReferenced):
            txn.check()
    finally:
        monkeypatch.undo()
        a.close()
        b.close()


@pytest.mark.skipif(os.name == "nt", reason="needs dir_fd to build a tree longer than the path limit")
def test_a_tree_longer_than_the_path_limit_under_sources_means_cannot_prove(tmp_path):
    a, b = _diverged_r2(tmp_path)
    try:
        fd = os.open(b.root / "sources", os.O_RDONLY)
        try:
            for _ in range(20):
                os.mkdir("d" * 250, dir_fd=fd)
                deeper = os.open("d" * 250, os.O_RDONLY, dir_fd=fd)
                os.close(fd)
                fd = deeper
        finally:
            os.close(fd)
        assert "unreadable" in b.scan().blocked.values()
        txn, _, _ = _replace(a, b)
        with pytest.raises(RevisionReferenced):
            txn.check()
    finally:
        a.close()
        b.close()


@pytest.mark.parametrize("source", ["world:notes.swp", "world:thumbs.db"])
def test_a_case_renamed_noise_named_source_is_blocked_never_absent(tmp_path, source):
    """``Notes.swp`` is a noise name, but it folds to the valid source folder ``notes.swp``."""
    path = "sources/" + "/".join(source.split(":")) + "/_source/manifest.json"
    with LibraryStore.create(tmp_path / "lib") as store:
        txn = store.transaction()
        txn.put(SourceManifest(source, OWNER, "N", {}, ()), None)
        txn.commit(owner_handle())
        folder = store.root / "sources" / "world" / source.split(":")[1]
        os.rename(folder, folder.with_name(folder.name.capitalize()))
        assert store.scan().blocked_reason(path) is not None


def test_a_case_rename_of_a_noise_named_source_is_never_read_as_a_deletion(tmp_path):
    desktop, phone = make_pair(tmp_path)
    try:
        txn = desktop.transaction()
        txn.put(SourceManifest("world:notes.swp", OWNER, "N", {}, ()), None)
        txn.commit(handle())
        sync(desktop, phone, handles())
        folder = phone.root / "sources" / "world" / "notes.swp"
        os.rename(folder, folder.with_name("Notes.swp"))
        conflict = sync(desktop, phone, handles()).conflict("sources/world/notes.swp/_source/manifest.json")
        assert conflict is not None and conflict.code != "deleted"
    finally:
        desktop.close()
        phone.close()


def test_a_pin_below_a_case_renamed_noise_named_source_still_counts(tmp_path):
    a, b = _diverged_r2(tmp_path)
    try:
        txn = b.transaction()
        txn.put(SourceManifest("world:notes.swp", OWNER, "N", {}, (), extra={"x-pin": f"{HARBOR}/character/wren@r2"}),
                None)
        txn.commit(owner_handle())
        with pytest.raises(RevisionReferenced):
            _replace(a, b)[0].check()
        folder = b.root / "sources" / "world" / "notes.swp"
        os.rename(folder, folder.with_name("Notes.swp"))
        with pytest.raises(RevisionReferenced):
            _replace(a, b)[0].check()
    finally:
        a.close()
        b.close()


def test_an_uninspectable_noise_named_identifier_folder_is_unreadable_not_skipped(tmp_path, monkeypatch):
    with LibraryStore.create(tmp_path / "lib") as store:
        txn = store.transaction()
        txn.put(SourceManifest("world:notes.swp", OWNER, "N", {}, ()), None)
        txn.commit(owner_handle())
        _refusing_lstat(monkeypatch, store.root / "sources" / "world" / "notes.swp")
        assert store.scan().blocked_reason("sources/world/notes.swp/_source/manifest.json") == "unreadable"


def test_recovery_never_publishes_a_replace_whose_hold_vanished(tmp_path):
    """A crash after a replace's commit point, then the hold's staged file removed while the app is
    down: the replace's own temp is still staged, so nothing was published, and recovery abandons both."""
    from grimoire.store.layout import conflict_parts
    a, b = _diverged_r2(tmp_path)
    chosen, replaced = (a.root / R2).read_bytes(), (b.root / R2).read_bytes()
    a.close()
    root = b.root
    b.close()
    r2 = tuple(R2.split("/"))
    slot = conflict_parts(r2, etag(replaced))
    (root / ".txn" / "recr2.new").write_bytes(chosen)  # the hold's "rechold.new" is gone
    journal = Journal("commit", (Entry(r2, "recr2.new", etag(replaced), etag(chosen), "replica"),
                                 Entry(slot, "rechold.new", None, etag(replaced), "hold")))
    (root / ".txn" / "journal.json").write_bytes(journal.render())
    with LibraryStore.open(root):
        pass
    assert (root / R2).read_bytes() == replaced


_STRAY = ["README.md", "Desktop.ini", "Notes.txt", *([] if os.name == "nt" else ["Icon\r"])]  # NTFS forbids \r


@pytest.mark.parametrize("folder, name", [*(("campaigns/c1/instances/character", name) for name in _STRAY),
                                          *(("sources/world/harbor", name) for name in [*_STRAY, "Notes.json"])])
def test_a_stray_file_anywhere_never_blocks_a_revision_replace(tmp_path, folder, name):
    """A regular file that is no record path cannot hold or pin one, wherever it sits. (``Notes.json``
    in an instances folder is different: on a folding filesystem it is the instance ``notes``.)"""
    a, b = _diverged_r2(tmp_path)
    try:
        (b.root / folder / name).write_bytes(b"note")
        txn, chosen, _ = _replace(a, b)
        txn.commit(owner_handle("sources", "campaigns", "kept"))
        assert (b.root / R2).read_bytes() == chosen
    finally:
        a.close()
        b.close()


def test_a_peer_folder_that_is_a_file_is_unreadable_not_absent(tmp_path):
    with LibraryStore.create(tmp_path / "lib") as store:
        (store.root / ".sync" / "peers").mkdir(parents=True)
        (store.root / ".sync" / "peers" / "phone").write_bytes(b"junk")
        with pytest.raises(OSError):
            store.read_sync_state("peers/phone/pending.json")


def test_check_refuses_a_lookalike_conflicts_folder(tmp_path):
    with LibraryStore.create(tmp_path / "lib") as store:
        (store.root / ".Conflicts").mkdir()
        try:
            with pytest.raises(NameCollision):
                store.transaction().check()
        finally:
            (store.root / ".Conflicts").rmdir()
