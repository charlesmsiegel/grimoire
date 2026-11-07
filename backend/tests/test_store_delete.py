"""The store's deletion (028): ``Transaction.delete_exact``, always paired with a hold of what it removes.

Its crash, torn-write, error and race explorations are in ``test_deletion_recovery.py``."""

from __future__ import annotations

import pytest

from grimoire.security import Approval, Authorizer, Grant, Permission, ResourcePath, payload_digest
from grimoire.store import (
    InvalidEnvelope, LibraryStore, RecordNotFound, RevisionReferenced, StaleWrite, WriteConflict, WriteDenied, etag,
)
from grimoire.store.journal import Entry, Journal, parse
from grimoire.store.errors import RecoveryFailed

from store_helpers import (
    DELETE_SCOPES as ALL, MANIFEST_PATH, OWNER, R1_PATH, WREN_A, WREN_A_PATH, WREN_B_PATH, owner_handle, seed,
)
from store_helpers import delete_held as _delete


@pytest.fixture
def store(tmp_path):
    with LibraryStore.create(tmp_path / "lib") as library:
        seed(library)
        yield library


def _kept(store: LibraryStore) -> list[bytes]:
    return sorted((store.root / path).read_bytes() for path in store.conflicts())


def test_a_deletion_removes_the_record_and_keeps_its_bytes_as_a_hold(store):
    data = (store.root / WREN_A_PATH).read_bytes()
    _delete(store, WREN_A_PATH)
    assert not (store.root / WREN_A_PATH).exists()
    with pytest.raises(RecordNotFound):
        store.read(WREN_A)
    assert WREN_A not in store.load().instances
    assert _kept(store) == [data]  # recoverable beside where it was, never just unlinked


def test_a_source_is_deleted_as_a_whole_in_one_transaction(store):
    paths = (R1_PATH, MANIFEST_PATH, WREN_A_PATH, WREN_B_PATH)  # both wrens pin r1, so they go in the same transaction
    removed = sorted((store.root / path).read_bytes() for path in paths)
    _delete(store, *paths)
    assert not any((store.root / path).exists() for path in paths)
    assert _kept(store) == removed
    assert "world:harbor" not in store.load().sources


def test_a_revision_a_record_staying_here_pins_is_never_deleted(store):
    """Checked under the lock at commit, whatever a sync planned earlier: wren-a and wren-b pin r1."""
    with pytest.raises(RevisionReferenced):
        _delete(store, R1_PATH, MANIFEST_PATH)
    assert (store.root / R1_PATH).exists() and (store.root / MANIFEST_PATH).exists() and _kept(store) == []


@pytest.mark.parametrize("keep", ["none", "regenerable", "other bytes"])
def test_a_deletion_without_a_hold_of_exactly_its_bytes_is_refused(store, keep):
    data = (store.root / WREN_A_PATH).read_bytes()
    txn = store.transaction()
    if keep == "regenerable":
        txn.keep_version(WREN_A_PATH, data, regenerable=True)
    elif keep == "other bytes":
        txn.keep_version(WREN_A_PATH, data + b" ", regenerable=False)
    txn.delete_exact(WREN_A_PATH, etag(data))
    with pytest.raises(InvalidEnvelope):
        txn.check()
    with pytest.raises(InvalidEnvelope):
        txn.commit(owner_handle(*ALL))
    assert (store.root / WREN_A_PATH).read_bytes() == data


def test_a_deletion_of_bytes_no_longer_there_is_stale(store):
    data = (store.root / WREN_A_PATH).read_bytes()
    txn = store.transaction()
    txn.keep_version(WREN_A_PATH, data, regenerable=False)
    txn.delete_exact(WREN_A_PATH, etag(data))
    (store.root / WREN_A_PATH).write_bytes(data.replace(b'"hp": 3', b'"hp": 8'))  # edited since
    with pytest.raises((StaleWrite, WriteConflict)):
        txn.commit(owner_handle(*ALL))
    assert b'"hp": 8' in (store.root / WREN_A_PATH).read_bytes()


@pytest.mark.parametrize("path", [
    "campaigns/c1/events/e1.json",  # 020 knowledge: create-only, a missing one is damage
    "campaigns/c1/transcripts/talk/0000000001.json",  # 036 transcript page: create-only
    "campaigns/c1/images/wren.json",  # 018 image object: create-only
    "assets/blobs/00/" + "0" * 64 + ".png",  # content-addressed
    "campaigns/c1/accepted/a1.json",  # held families are never touched
    ".sync/peers/phone/archive.json", "notes.txt", 7,
])
def test_only_the_families_sync_may_delete_can_be_deleted(store, path):
    with pytest.raises(InvalidEnvelope):
        store.transaction().delete_exact(path, etag(b"x"))


def test_a_deletion_names_an_etag(store):
    with pytest.raises(InvalidEnvelope):
        store.transaction().delete_exact(WREN_A_PATH, "not an etag")


def test_a_deletion_is_authorized_on_its_own_address_and_never_by_an_approval_to_write(store):
    """``preview`` shows the deletion document, which an approval must cover: one for the record's
    bytes (an approval to write it) never removes it."""
    data = (store.root / WREN_A_PATH).read_bytes()

    def staged():
        txn = store.transaction()
        txn.keep_version(WREN_A_PATH, data, regenerable=False)
        return txn, txn.delete_exact(WREN_A_PATH, etag(data))
    txn, key = staged()
    document = txn.preview()[key]
    assert key == WREN_A_PATH and document != data  # the same address a write of the record is approved at
    assert b"grimoire.deletion/1" in document and etag(data).encode() in document
    with pytest.raises(WriteDenied):  # no grant over campaigns
        txn.commit(owner_handle("sources", "kept"))
    assert (store.root / WREN_A_PATH).read_bytes() == data
    authority = Authorizer([OWNER])
    for number, scope in enumerate(ALL):
        authority.add_grant(Grant(f"g{number}", "app:sync", frozenset({Permission.APPLY}), ResourcePath.parse(scope),
                                  OWNER))
    txn, _ = staged()
    hold = next(other for other in txn.staged() if other != key)
    held = txn.preview()[hold]
    for number, (where, payload) in enumerate(((key, data), (key, document), (hold, held), (hold, held))):
        authority.register_approval(Approval(f"a{number}", "app:sync", Permission.APPLY, ResourcePath.parse(where),
                                             payload_digest(payload), OWNER))
    with pytest.raises(WriteDenied):
        txn.commit(authority.handle_for("app:sync"), {key: "a0", hold: "a2"})
    assert (store.root / WREN_A_PATH).read_bytes() == data
    txn, _ = staged()
    txn.commit(authority.handle_for("app:sync"), {key: "a1", hold: "a3"})
    assert not (store.root / WREN_A_PATH).exists()


def test_a_removal_entry_is_journaled_only_for_a_deletable_record():
    tag = etag(b"x")
    data = Journal("commit", (Entry(tuple(WREN_A_PATH.split("/")), "rec~tmp1.new", tag, etag(b"d"), "remove"),)).render()
    assert parse(data).entries[0].kind == "remove"
    for target, old in [("campaigns/c1/events/e1.json", tag), (WREN_A_PATH, None),
                        (".sync/peers/phone/archive.json", tag)]:
        bad = Journal("commit", (Entry(tuple(target.split("/")), "rec~tmp1.new", old, etag(b"d"), "remove"),)).render()
        with pytest.raises(RecoveryFailed):
            parse(bad)
