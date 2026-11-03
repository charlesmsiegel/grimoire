"""Relation records in the native store (Sprint 017): the same guarded transaction as 010 and 107.

Covers the additive family ``campaigns/<id>/relations/<relation_id>.json`` on
``grimoire.scope-record/1``: canonical paths, the widened address union, CAS, authorization,
collisions, links, containment on load, ``campaign_relations`` parity with ``load()``, recovery
and the rollback path. All fixtures are synthetic.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from grimoire.core.identity import ChatRef, RelationRef
from grimoire.library import LibraryPathError
from grimoire.store import (
    CampaignRelations, InvalidEnvelope, LibraryStore, LinkRefused, NameCollision, PendingRecovery, ProcessLocker,
    RecordNotFound, ScopeRecord, StaleWrite, UnstorableIdentifier, WriteDenied,
)
from grimoire.store.envelope import EnvelopeError, parse_scope_record, scope_record_document
from grimoire.store.layout import RESERVED_CAMPAIGN_ENTRIES, classify, relation_parts
from grimoire.testing import Crash, RootedFileOps, Target
from grimoire.testing.fileops import snapshot

from store_helpers import OWNER, make_dir_link, owner_handle, seed

REL = RelationRef("c1", "r-1")
REL_PATH = "campaigns/c1/relations/r-1.json"


def relation_record(ref=REL, note="a") -> ScopeRecord:
    return ScopeRecord(ref, "test.relation/1", {"note": note}, owner=OWNER)


@pytest.fixture
def library(tmp_path):
    store = LibraryStore.create(tmp_path / "lib")
    seed(store)
    yield store
    store.close()


def _commit(store, *puts, handle=None):
    txn = store.transaction()
    for envelope, expected in puts:
        txn.put(envelope, expected)
    return txn.commit(handle or owner_handle())


def _write(root: Path, relative: str, content) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content if isinstance(content, bytes) else json.dumps(content).encode())


# -- layout and envelope ------------------------------------------------------------------------

def test_relation_paths_classify_exactly():
    assert relation_parts(REL) == ("campaigns", "c1", "relations", "r-1.json")
    assert classify(relation_parts(REL)) == ("relation", REL)
    assert "relations" not in RESERVED_CAMPAIGN_ENTRIES


@pytest.mark.parametrize("parts", [
    ("campaigns", "c1", "relations", "r-1.JSON"), ("campaigns", "c1", "relations", "R-1.json"),
    ("campaigns", "c1", "Relations", "r-1.json"), ("campaigns", "c1", "relations", "x", "r.json"),
    ("campaigns", "c1", "relations.json"), ("campaigns", "c1", "relations", "r-1"),
])
def test_look_alike_relation_paths_are_not_records(parts):
    assert classify(parts) is None


def test_relation_id_needs_room_for_the_file_suffix():
    assert relation_parts(RelationRef("c1", "r" * 123))
    with pytest.raises(UnstorableIdentifier):
        relation_parts(RelationRef("c1", "r" * 124))


def test_scope_record_accepts_a_relation_address_and_round_trips():
    record = ScopeRecord(REL, "test.relation/1", {"a": 1}, owner=OWNER, extra={"x-note": True})
    document = scope_record_document(record, None)
    assert document["address"] == "campaign:c1#relation/r-1"
    assert parse_scope_record(document) == (record, None)


@pytest.mark.parametrize("address, expected", [
    ("campaign:c1#relation/r-1", REL), ("campaign:c1#chat/t-1", ChatRef("c1", "t-1")),
])
def test_scope_record_address_dispatch_is_exact(address, expected):
    document = {"format": "grimoire.scope-record/1", "address": address, "schema": "s", "payload": {}}
    assert parse_scope_record(document)[0].address == expected


@pytest.mark.parametrize("address", ["campaign:c1#other/x", "campaign:c1#relation/R1", "campaign:c1#relations/x"])
def test_an_unknown_fragment_is_malformed(address):
    document = {"format": "grimoire.scope-record/1", "address": address, "schema": "s", "payload": {}}
    with pytest.raises(EnvelopeError) as error:
        parse_scope_record(document)
    assert error.value.code == "malformed"


def test_a_relation_address_is_still_a_scope_record_address():
    with pytest.raises(InvalidEnvelope):
        ScopeRecord("campaign:c1#relation/r-1", "s", {})  # text is not an address value


# -- the guarded transaction ------------------------------------------------------------------

def test_relation_record_path_and_round_trip(library):
    txn = library.transaction()
    assert txn.put(relation_record(), None) == REL_PATH
    txn.commit(owner_handle())
    stored = library.read(REL)
    assert stored.record == relation_record()
    assert set(library.load().relations) == {REL}


def test_relation_write_is_cas_guarded(library):
    result = _commit(library, (relation_record(), None))
    with pytest.raises(StaleWrite):
        _commit(library, (relation_record(note="b"), None))
    _commit(library, (relation_record(note="b"), result.etags[REL_PATH]))
    assert library.read(REL).record.payload == {"note": "b"}


def test_relation_write_needs_apply_permission(library):
    before = snapshot(library.root)
    with pytest.raises(WriteDenied):
        _commit(library, (relation_record(), None), handle=owner_handle("campaigns/c1/chats"))
    assert snapshot(library.root) == before
    _commit(library, (relation_record(), None), handle=owner_handle("campaigns/c1/relations"))


@pytest.mark.parametrize("existing", ["campaigns/c1/relations/R-1.json", "campaigns/c1/Relations/r-1.json"])
def test_a_look_alike_existing_entry_is_a_collision_at_commit(library, existing):
    _write(library.root, existing, b"{}")
    txn = library.transaction()
    txn.put(relation_record(), None)  # put never looks at the disk
    with pytest.raises(NameCollision):
        txn.commit(owner_handle())


def test_a_linked_relations_folder_is_never_written_or_followed(library, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "r-1.json").write_text("{}")
    (library.root / "campaigns" / "c1").mkdir(parents=True, exist_ok=True)
    make_dir_link(library.root / "campaigns" / "c1" / "relations", outside)
    before = snapshot(outside)
    with pytest.raises(LibraryPathError):
        _commit(library, (relation_record(), None))
    view = library.load()
    assert ("campaigns/c1/relations", "link") in {(p.path, p.code) for p in view.problems}
    assert view.relations == {}
    found = library.campaign_relations("c1")
    assert found.records == {}
    assert ("campaigns/c1/relations", "link") in {(p.path, p.code) for p in found.problems}
    with pytest.raises(LinkRefused):
        library.read(REL)
    assert snapshot(outside) == before


# -- loading ----------------------------------------------------------------------------------

def test_relation_lookalikes_and_strays_are_reported(library):
    _commit(library, (relation_record(), None))
    root = library.root
    _write(root, "campaigns/c1/relations/R-2.json", b"{}")
    _write(root, "campaigns/c1/relations/notes.txt", b"hello")
    _write(root, "campaigns/c1/relations/sub/x.json", b"{}")
    view = library.load()
    codes = {(p.path, p.code) for p in view.problems}
    assert ("campaigns/c1/relations/R-2.json", "name_mismatch") in codes
    assert ("campaigns/c1/relations/notes.txt", "unrecognized") in codes
    assert ("campaigns/c1/relations/sub", "unrecognized") in codes
    assert set(view.relations) == {REL}


def test_relations_file_not_folder_is_unrecognized(library):
    _write(library.root, "campaigns/c1/relations", b"a file")
    view = library.load()
    assert ("campaigns/c1/relations", "unrecognized") in {(p.path, p.code) for p in view.problems}
    assert library.campaign_relations("c1").records == {}


def _bad_files(library) -> None:
    root = library.root
    _commit(library, (relation_record(), None), (relation_record(RelationRef("c2", "r-9")), None))
    _write(root, "campaigns/c1/relations/broken.json", b"{not json")
    _write(root, "campaigns/c1/relations/newer.json",
           {"format": "grimoire.scope-record/9", "address": "campaign:c1#relation/newer", "schema": "s",
            "payload": {}})
    copied = json.loads((root / REL_PATH).read_bytes())
    _write(root, "campaigns/c1/relations/r-2.json", copied)  # claims r-1
    _write(root, "campaigns/c1/relations/R-3.json", b"{}")


def test_malformed_newer_and_misaddressed_relation_files_are_contained(library):
    _bad_files(library)
    view = library.load()
    codes = {(p.path, p.code) for p in view.problems}
    assert ("campaigns/c1/relations/broken.json", "malformed") in codes
    assert ("campaigns/c1/relations/newer.json", "unsupported_format") in codes
    assert ("campaigns/c1/relations/r-2.json", "address_mismatch") in codes
    assert set(view.relations) == {REL, RelationRef("c2", "r-9")}
    with pytest.raises(RecordNotFound):
        library.read(RelationRef("c1", "absent"))


def test_campaign_relations_matches_load(library):
    _bad_files(library)
    view = library.load()
    found = library.campaign_relations("c1")
    assert isinstance(found, CampaignRelations)
    assert dict(found.records) == {k: v for k, v in view.relations.items() if k.campaign_id == "c1"}
    prefix = "campaigns/c1/relations"
    assert sorted((p.path, p.code) for p in found.problems) == sorted(
        (p.path, p.code) for p in view.problems if p.path.startswith(prefix))
    assert library.campaign_relations("nowhere") == CampaignRelations({}, ())


def test_libraries_without_relations_load_as_before(library):
    view = library.load()
    assert view.relations == {} and view.problems == ()


# -- recovery and rollback ----------------------------------------------------------------------

def _crashed_copy(root: Path, name: str, locker: ProcessLocker) -> Path:
    lib = root / name
    shutil.copytree(root / "lib", lib)
    ops = RootedFileOps(root, plan=[Crash(Target("replace", target=f"{name}/.txn/journal.json"), "after")],
                        record=False)
    with pytest.raises(BaseException):
        with LibraryStore.open(lib, ops=ops, locker=locker) as store:
            _commit(store, (relation_record(), None))
    return lib


def test_a_journal_naming_a_relation_recovers_or_can_be_discarded(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    locker = ProcessLocker()
    with LibraryStore.create(root / "lib", locker=locker) as store:
        seed(store)
    lib = _crashed_copy(root, "lib-recover", locker)
    locker = ProcessLocker()
    with LibraryStore.open(lib, writable=False, locker=locker) as reader:
        with pytest.raises(PendingRecovery):
            reader.campaign_relations("c1")
    with LibraryStore.open(lib, locker=locker) as store:
        assert REL_PATH in {target for target, _ in store.last_recovery.published}
        assert store.read(REL).record == relation_record()
    lib = _crashed_copy(root, "lib-discard", ProcessLocker())
    locker = ProcessLocker()
    LibraryStore.discard_journal(lib, locker=locker)  # what a pre-017 build's user runs
    with LibraryStore.open(lib, locker=locker) as store:
        assert store.load().relations == {}
        assert any("discarded" in path for path in store.conflicts())
