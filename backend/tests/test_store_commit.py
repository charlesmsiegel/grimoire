"""Native store writes: round trip, revisions, compare-and-swap, authorization and the writer lock."""

from __future__ import annotations

import errno
import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from grimoire.core.identity import InstanceRef, SourceRef
from grimoire.library import LibraryPathError
from grimoire.security import Approval, Authorizer, Grant, Permission, ResourcePath, payload_digest
from grimoire.store import (
    CrossDevice, ImmutableRevision, InvalidEnvelope, InvalidSourceState, LibraryLocked, LibraryStore,
    LockUnavailable, NameCollision, NotALibrary, PendingRecovery, RecordEnvelope, RecordNotFound,
    SourceManifest, StaleWrite, StoreError, UnstorableIdentifier, WriteDenied, etag,
)
from grimoire.testing.fileops import snapshot

from store_helpers import (
    HARBOR, OWNER, WREN, WREN_A, WREN_B, WREN_R1, authorizer, harbor_manifest, instance, owner_handle, seed,
    wren,
)


@pytest.fixture
def library(tmp_path):
    store = LibraryStore.create(tmp_path / "lib")  # real DiskOps and OsLocker
    seed(store)
    yield store
    store.close()


def _files(root: Path) -> dict[str, bytes]:
    return {key: value for key, value in snapshot(root).items() if value is not None}


def test_definition_and_two_instances_round_trip_through_a_reopen(tmp_path):
    with LibraryStore.create(tmp_path / "lib") as store:
        etags = seed(store)
    with LibraryStore.open(tmp_path / "lib") as store:
        source = store.read_source(HARBOR)
        assert source.manifest == harbor_manifest() and source.previous is None
        assert source.manifest.extra["x-notes"] == "kept verbatim"
        record = store.read(WREN_R1)
        assert record.envelope == wren("r1") and record.envelope.extra["x-author-note"]["draft"] is True
        assert record.etag == etags["sources/world/harbor/_source/records/character/wren/r1.json"]
        a, b = store.read(WREN_A).envelope, store.read(WREN_B).envelope
        assert (a.derived_from, b.derived_from) == (str(WREN_R1), str(WREN))
        assert a.base == b.base == str(WREN_R1)


def test_a_successful_commit_creates_exactly_the_expected_entries(tmp_path):
    with LibraryStore.create(tmp_path / "lib") as store:
        seed(store)
    assert sorted(snapshot(tmp_path / "lib")) == [
        ".txn", ".txn/lock", "campaigns", "campaigns/c1", "campaigns/c1/instances",
        "campaigns/c1/instances/character", "campaigns/c1/instances/character/wren-a.json", "campaigns/c2",
        "campaigns/c2/instances", "campaigns/c2/instances/character",
        "campaigns/c2/instances/character/wren-b.json", "grimoire-library.json", "sources", "sources/world",
        "sources/world/harbor", "sources/world/harbor/_source", "sources/world/harbor/_source/manifest.json",
        "sources/world/harbor/_source/records", "sources/world/harbor/_source/records/character",
        "sources/world/harbor/_source/records/character/wren",
        "sources/world/harbor/_source/records/character/wren/r1.json",
    ]


def test_preview_is_exactly_what_is_written(library):
    txn = library.transaction()
    address = txn.put(instance(InstanceRef("c3", "character", "wren-c"), WREN), None)
    preview = txn.preview()
    result = txn.commit(owner_handle())
    assert (library.root / address).read_bytes() == preview[address]
    assert result.etags[address] == etag(preview[address])
    assert json.loads(preview[address])["previous"] is None


def test_divergent_instances_and_the_previous_chain(library):
    before_b = (library.root / "campaigns/c2/instances/character/wren-b.json").read_bytes()
    first = library.read(WREN_A)
    txn = library.transaction()
    txn.put(instance(WREN_A, WREN_R1, hp=1), first.etag)
    txn.commit(owner_handle())
    second = library.read(WREN_A)
    assert second.previous == first.etag and second.envelope.payload["hp"] == 1
    assert (library.root / "campaigns/c2/instances/character/wren-b.json").read_bytes() == before_b


def test_a_stale_write_is_rejected_and_keeps_its_approval(library):
    current = library.read(WREN_A).etag
    winner = library.transaction()
    winner.put(instance(WREN_A, WREN_R1, hp=1), current)
    winner.commit(owner_handle())

    authority = Authorizer([OWNER])
    authority.add_grant(Grant("g", "app:sync", frozenset({Permission.APPLY}), ResourcePath.parse("campaigns"),
                              OWNER))
    loser = library.transaction()
    address = loser.put(instance(WREN_A, WREN_R1, hp=9), current)
    authority.register_approval(Approval("a1", "app:sync", Permission.APPLY, ResourcePath.parse(address),
                                         payload_digest(loser.preview()[address]), OWNER))
    with pytest.raises(StaleWrite) as stale:
        loser.commit(authority.handle_for("app:sync"), {address: "a1"})
    assert stale.value.stale == ((address, "changed"),)
    assert library.read(WREN_A).envelope.payload["hp"] == 1
    assert [approval.approval_id for approval in authority.pending_approvals()] == ["a1"]
    assert authority.audit() == ()

    create_again = library.transaction()
    create_again.put(instance(WREN_B, WREN), None)
    with pytest.raises(StaleWrite, match="exists"):
        create_again.commit(owner_handle())


def test_definition_revisions_are_immutable(library):
    with pytest.raises(ImmutableRevision):
        library.transaction().put(wren("r1", "rewritten"), library.read(WREN_R1).etag)


def test_a_newer_format_is_never_overwritten(library):
    path = library.root / "campaigns/c1/instances/character/wren-a.json"
    document = json.loads(path.read_bytes())
    document["format"] = "grimoire.record/2"
    path.write_text(json.dumps(document))
    txn = library.transaction()
    txn.put(instance(WREN_A, WREN_R1), etag(path.read_bytes()))
    with pytest.raises(StaleWrite, match="unsupported_format"):
        txn.commit(owner_handle())


def test_a_differently_spelled_existing_entry_is_a_collision(library):
    (library.root / "campaigns" / "C9").mkdir()
    (library.root / "campaigns" / "C9" / "notes.txt").write_text("hand made")
    before = snapshot(library.root)
    txn = library.transaction()
    txn.put(instance(InstanceRef("c9", "character", "x"), WREN), None)
    with pytest.raises(NameCollision):
        txn.commit(owner_handle())
    assert snapshot(library.root) == before


def test_duplicate_targets_and_empty_transactions_are_invalid(library):
    txn = library.transaction()
    txn.put(instance(WREN_A, WREN_R1), None)
    with pytest.raises(InvalidEnvelope):
        txn.put(instance(WREN_A, WREN_R1, hp=2), None)
    with pytest.raises(InvalidEnvelope):
        library.transaction().commit(owner_handle())


# -- authorization ------------------------------------------------------------------

def _importer(scope: str = "campaigns") -> Authorizer:
    authority = Authorizer([OWNER])
    authority.add_grant(Grant("g", "app:importer", frozenset({Permission.APPLY}), ResourcePath.parse(scope),
                              OWNER))
    return authority


def test_unauthorized_and_unapproved_writes_change_nothing(library):
    before = snapshot(library.root)
    txn = library.transaction()
    address = txn.put(instance(InstanceRef("c3", "character", "x"), WREN), None)
    nobody = Authorizer([OWNER])
    with pytest.raises(WriteDenied):
        txn.commit(nobody.handle_for(OWNER, trusted=True))
    authority = _importer()
    txn = library.transaction()
    txn.put(instance(InstanceRef("c3", "character", "x"), WREN), None)
    with pytest.raises(WriteDenied):
        txn.commit(authority.handle_for("app:importer"))  # untrusted, no approval
    authority.register_approval(Approval("a1", "app:importer", Permission.APPLY, ResourcePath.parse(address),
                                         payload_digest(b"other bytes"), OWNER))
    txn = library.transaction()
    txn.put(instance(InstanceRef("c3", "character", "x"), WREN), None)
    with pytest.raises(WriteDenied):
        txn.commit(authority.handle_for("app:importer"), {address: "a1"})
    assert snapshot(library.root) == before
    assert [record.reason.value for record in authority.audit()] == ["approval_required", "approval_required"]


def test_an_approved_untrusted_write_succeeds_once(library):
    authority = _importer()
    txn = library.transaction()
    address = txn.put(instance(InstanceRef("c3", "character", "x"), WREN), None)
    authority.register_approval(Approval("a1", "app:importer", Permission.APPLY, ResourcePath.parse(address),
                                         payload_digest(txn.preview()[address]), OWNER))
    txn.commit(authority.handle_for("app:importer"), {address: "a1"})
    assert authority.pending_approvals() == ()
    assert library.read(InstanceRef("c3", "character", "x")).envelope.derived_from == str(WREN)


def test_a_partial_denial_writes_nothing_and_lists_consumed_approvals(library):
    authority = _importer()
    txn = library.transaction()
    refs = [InstanceRef("c3", "character", name) for name in ("one", "two", "three")]
    addresses = [txn.put(instance(ref, WREN), None) for ref in refs]
    for number, address in enumerate(addresses):
        if number != 1:  # the second target has no approval
            authority.register_approval(Approval(f"a{number}", "app:importer", Permission.APPLY,
                                                 ResourcePath.parse(address),
                                                 payload_digest(txn.preview()[address]), OWNER))
    with pytest.raises(WriteDenied) as denied:
        txn.commit(authority.handle_for("app:importer"), {addresses[0]: "a0", addresses[2]: "a2"})
    assert denied.value.consumed_approvals == ("a0", "a2")  # every target is decided and audited
    assert len(denied.value.decisions) == 3
    assert not (library.root / "campaigns" / "c3").exists()


def test_a_grant_on_one_source_does_not_cover_its_sub_sources(library):
    handle = owner_handle("sources/world/harbor/_source")
    txn = library.transaction()
    txn.put(wren("r9"), None)
    txn.put(harbor_manifest({"character/wren": "r9"}), library.read_source(HARBOR).etag)
    txn.commit(handle)
    sub = library.transaction()
    sub.put(SourceManifest("world:harbor:dlc", OWNER), None)
    with pytest.raises(WriteDenied):
        sub.commit(handle)


@pytest.mark.parametrize("failure", ["collision", "unstorable", "source-state", "cross-device", "too-long"])
def test_deterministic_refusals_happen_before_authorization(tmp_path, monkeypatch, failure):
    store = LibraryStore.create(tmp_path / "lib")
    seed(store)
    authority = _importer("sources")
    txn = store.transaction()
    if failure == "collision":
        (tmp_path / "lib" / "sources" / "world" / "Harbor2").mkdir()
        (tmp_path / "lib" / "sources" / "world" / "Harbor2" / "x").write_text("x")
        txn.put(SourceManifest("world:harbor2", OWNER), None)
        expected = NameCollision
    elif failure == "unstorable":
        (tmp_path / "lib" / "sources" / "world" / "harbor2").write_text("a file, not a directory")
        txn.put(SourceManifest("world:harbor2", OWNER), None)
        expected = UnstorableIdentifier
    elif failure == "source-state":
        txn.put(wren("r2"), None)  # a second revision without a head move
        expected = InvalidSourceState
    elif failure == "cross-device":
        txn.put(SourceManifest("world:harbor2", OWNER), None)
        from grimoire.store import guard
        real = guard._stat
        world = tmp_path / "lib" / "sources" / "world"

        def other_device(path):  # only the guard's view: the target's directory is on another device
            info = real(path)
            if info is not None and Path(path) == world:
                return os.stat_result((info.st_mode, info.st_ino, info.st_dev + 1, *tuple(info)[3:]))
            return info
        monkeypatch.setattr(guard, "_stat", other_device)
        expected = CrossDevice
    else:
        store.close()
        store = LibraryStore.open(tmp_path / "lib", limits=lambda directory: (len(str(tmp_path)) + 40, None))
        txn = store.transaction()
        txn.put(SourceManifest("world:harbor2", OWNER), None)
        expected = UnstorableIdentifier
    address = next(iter(txn.preview()))
    authority.register_approval(Approval("a1", "app:importer", Permission.APPLY, ResourcePath.parse(address),
                                         payload_digest(txn.preview()[address]), OWNER))
    with pytest.raises(expected):
        txn.commit(authority.handle_for("app:importer"), {address: "a1"})
    assert authority.audit() == ()
    assert [approval.approval_id for approval in authority.pending_approvals()] == ["a1"]
    store.close()


# -- sessions and the writer lock -------------------------------------------------------

def test_create_and_open_refuse_what_is_not_a_fresh_library(tmp_path):
    with pytest.raises(LibraryPathError):
        LibraryStore.create(tmp_path / "missing-parent" / "lib")
    (tmp_path / "full").mkdir()
    (tmp_path / "full" / "notes.txt").write_text("mine")
    with pytest.raises(LibraryPathError):
        LibraryStore.create(tmp_path / "full")
    with pytest.raises(NotALibrary):
        LibraryStore.open(tmp_path / "full")
    with pytest.raises(LibraryPathError):
        LibraryStore.open(tmp_path / "missing")


def test_one_writer_at_a_time_and_readers_alongside(tmp_path):
    with LibraryStore.create(tmp_path / "lib"):
        with pytest.raises(LibraryLocked):
            LibraryStore.open(tmp_path / "lib")
        with LibraryStore.open(tmp_path / "lib", writable=False) as reader:
            assert reader.load().problems == ()
            with pytest.raises(StoreError):
                reader.transaction()
    LibraryStore.open(tmp_path / "lib").close()  # released on close


def test_the_writer_lock_is_held_across_processes_and_dies_with_its_holder(tmp_path):
    LibraryStore.create(tmp_path / "lib").close()
    src = Path(__file__).resolve().parents[1] / "src"
    child = subprocess.Popen(
        [sys.executable, "-c", textwrap.dedent(f"""
            import sys, time
            sys.path.insert(0, {str(src)!r})
            from grimoire.store import LibraryStore
            store = LibraryStore.open({str(tmp_path / 'lib')!r})
            print("held", flush=True)
            time.sleep(60)
        """)], stdout=subprocess.PIPE, text=True, env={**os.environ, "PYTHONPATH": str(src)})
    try:
        assert child.stdout.readline().strip() == "held"
        with pytest.raises(LibraryLocked):
            LibraryStore.open(tmp_path / "lib")
    finally:
        child.kill()
        child.wait()
    LibraryStore.open(tmp_path / "lib").close()


@pytest.mark.skipif(os.name == "nt", reason="POSIX flock")
def test_a_filesystem_that_cannot_lock_opens_read_only_only(tmp_path, monkeypatch):
    import fcntl
    LibraryStore.create(tmp_path / "lib").close()

    def no_locks(*args):
        raise OSError(errno.ENOLCK, "no locks available")
    monkeypatch.setattr(fcntl, "flock", no_locks)
    with pytest.raises(LockUnavailable):
        LibraryStore.open(tmp_path / "lib")
    with LibraryStore.open(tmp_path / "lib", writable=False) as reader:
        assert reader.load().problems == ()


def test_reads_refuse_while_a_commit_awaits_recovery(library):
    (library.root / ".txn" / "journal.json").write_bytes(b"{}")
    with LibraryStore.open(library.root, writable=False) as reader:
        with pytest.raises(PendingRecovery):
            reader.load()
        with pytest.raises(PendingRecovery):
            reader.read(WREN_R1)
    (library.root / ".txn" / "journal.json").unlink()


def test_reads_are_by_exact_address(library):
    with pytest.raises(RecordNotFound):
        library.read(WREN.at("r9"))
    with pytest.raises(ValueError):
        library.read(WREN)


def test_a_look_alike_conflicts_folder_refuses_a_commit_before_authorization(library):
    (library.root / ".CONFLICTS").mkdir()
    (library.root / ".CONFLICTS" / "notes.txt").write_text("a human's folder")
    authority = authorizer()
    txn = library.transaction()
    txn.put(instance(InstanceRef("c3", "character", "x"), WREN), None)
    with pytest.raises(NameCollision):
        txn.commit(authority.handle_for(OWNER, trusted=True))
    assert authority.audit() == ()
