"""Accepted-event and wrap-up records in the native store (Sprint 021): the same guarded transaction as 017.

Covers the additive families ``campaigns/<id>/accepted/<event_id>.json`` and
``campaigns/<id>/wrapups/<wrapup_id>.json`` on ``grimoire.scope-record/1``: canonical paths, the widened
address union, CAS, authorization, collisions, links, containment on load, ``campaign_accepted`` /
``campaign_wrapups`` parity with ``load()``, recovery and the rollback path. Synthetic fixtures only.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from grimoire.core.identity import AcceptedRef, RelationRef, WrapUpRef
from grimoire.library import LibraryPathError
from grimoire.store import (
    CampaignRecords, LibraryStore, LinkRefused, NameCollision, PendingRecovery, ProcessLocker, RecordNotFound,
    ScopeRecord, StaleWrite, UnstorableIdentifier, WriteDenied,
)
from grimoire.store.envelope import EnvelopeError, parse_scope_record, scope_record_document
from grimoire.store.layout import RESERVED_CAMPAIGN_ENTRIES, accepted_parts, classify, wrapup_parts
from grimoire.testing import Crash, RootedFileOps, Target
from grimoire.testing.fileops import snapshot

from store_helpers import OWNER, make_dir_link, owner_handle, seed

ACC, WRAP = AcceptedRef("c1", "e-1"), WrapUpRef("c1", "w-1")
FAMILIES = {
    "accepted": (ACC, "campaigns/c1/accepted/e-1.json", lambda s, c: s.campaign_accepted(c), "accepted"),
    "wrapups": (WRAP, "campaigns/c1/wrapups/w-1.json", lambda s, c: s.campaign_wrapups(c), "wrapups"),
}


def record(ref, note="a") -> ScopeRecord:
    return ScopeRecord(ref, "test.effect/1", {"note": note}, owner=OWNER)


def other(ref, campaign="c1", rid="x-2"):
    return type(ref)(campaign, rid)


@pytest.fixture
def library(tmp_path):
    store = LibraryStore.create(tmp_path / "lib")
    seed(store)
    yield store
    store.close()


@pytest.fixture(params=sorted(FAMILIES))
def family(request):
    return FAMILIES[request.param]


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

def test_paths_classify_exactly():
    assert accepted_parts(ACC) == ("campaigns", "c1", "accepted", "e-1.json")
    assert wrapup_parts(WRAP) == ("campaigns", "c1", "wrapups", "w-1.json")
    assert classify(accepted_parts(ACC)) == ("accepted", ACC)
    assert classify(wrapup_parts(WRAP)) == ("wrapup", WRAP)
    assert not {"accepted", "wrapups"} & RESERVED_CAMPAIGN_ENTRIES


@pytest.mark.parametrize("parts", [
    ("campaigns", "c1", "accepted", "e-1.JSON"), ("campaigns", "c1", "Accepted", "e-1.json"),
    ("campaigns", "c1", "accepted", "x", "e.json"), ("campaigns", "c1", "accepted.json"),
    ("campaigns", "c1", "wrapups", "W-1.json"), ("campaigns", "c1", "wrapup", "w-1.json"),
])
def test_look_alike_paths_are_not_records(parts):
    assert classify(parts) is None


def test_ids_need_room_for_the_file_suffix():
    assert accepted_parts(AcceptedRef("c1", "e" * 123))
    with pytest.raises(UnstorableIdentifier):
        wrapup_parts(WrapUpRef("c1", "w" * 124))


@pytest.mark.parametrize("ref", [ACC, WRAP])
def test_scope_record_round_trips_and_dispatches_exactly(ref):
    rec = ScopeRecord(ref, "s/1", {"a": 1}, owner=OWNER, extra={"x-note": True})
    document = scope_record_document(rec, None)
    assert document["address"] == str(ref)
    assert parse_scope_record(document) == (rec, None)


@pytest.mark.parametrize("address", ["campaign:c1#accepted/E1", "campaign:c1#wrapup/", "campaign:c1#accepteds/x"])
def test_a_bad_fragment_is_malformed(address):
    document = {"format": "grimoire.scope-record/1", "address": address, "schema": "s", "payload": {}}
    with pytest.raises(EnvelopeError) as error:
        parse_scope_record(document)
    assert error.value.code == "malformed"


# -- the guarded transaction ------------------------------------------------------------------

def test_round_trip_and_load(library, family):
    ref, path, _, attribute = family
    txn = library.transaction()
    assert txn.put(record(ref), None) == path
    txn.commit(owner_handle())
    assert library.read(ref).record == record(ref)
    assert set(getattr(library.load(), attribute)) == {ref}


def test_cas_guarded(library, family):
    ref, path, _, _ = family
    result = _commit(library, (record(ref), None))
    with pytest.raises(StaleWrite):
        _commit(library, (record(ref, "b"), None))
    _commit(library, (record(ref, "b"), result.etags[path]))
    assert library.read(ref).record.payload == {"note": "b"}


def test_needs_apply_permission(library, family):
    ref, path, _, _ = family
    before = snapshot(library.root)
    with pytest.raises(WriteDenied):
        _commit(library, (record(ref), None), handle=owner_handle("campaigns/c1/relations"))
    assert snapshot(library.root) == before
    _commit(library, (record(ref), None), handle=owner_handle(path.rsplit("/", 1)[0]))


def test_mixed_transaction_with_a_relation(library):
    rel = ScopeRecord(RelationRef("c1", "r-1"), "test.relation/1", {}, owner=OWNER)
    _commit(library, (record(ACC), None), (rel, None))
    view = library.load()
    assert set(view.accepted) == {ACC} and set(view.relations) == {RelationRef("c1", "r-1")}


def test_a_look_alike_existing_entry_is_a_collision(library, family):
    ref, path, _, _ = family
    folder, name = path.rsplit("/", 1)
    _write(library.root, folder + "/" + name.upper().replace(".JSON", ".json"), b"{}")
    txn = library.transaction()
    txn.put(record(ref), None)
    with pytest.raises(NameCollision):
        txn.commit(owner_handle())


def test_a_linked_folder_is_never_written_or_followed(library, family, tmp_path):
    ref, path, lister, attribute = family
    folder = path.rsplit("/", 1)[0]
    outside = tmp_path / "outside"
    outside.mkdir()
    (library.root / "campaigns" / "c1").mkdir(parents=True, exist_ok=True)
    make_dir_link(library.root / folder, outside)
    with pytest.raises(LibraryPathError):
        _commit(library, (record(ref), None))
    view = library.load()
    assert (folder, "link") in {(p.path, p.code) for p in view.problems}
    assert getattr(view, attribute) == {}
    found = lister(library, "c1")
    assert found.records == {} and (folder, "link") in {(p.path, p.code) for p in found.problems}
    with pytest.raises(LinkRefused):
        library.read(ref)
    assert list(outside.iterdir()) == []


# -- loading ----------------------------------------------------------------------------------

def _bad_files(library, ref, path) -> str:
    root = library.root
    folder = path.rsplit("/", 1)[0]
    _commit(library, (record(ref), None), (record(other(ref, "c2", "x-9")), None))
    _write(root, f"{folder}/broken.json", b"{not json")
    _write(root, f"{folder}/newer.json", {"format": "grimoire.scope-record/9",
                                          "address": str(other(ref, rid="newer")), "schema": "s", "payload": {}})
    _write(root, f"{folder}/x-2.json", json.loads((root / path).read_bytes()))  # claims the first ref
    _write(root, f"{folder}/X-3.json", b"{}")
    _write(root, f"{folder}/notes.txt", b"hello")
    return folder


def test_bad_files_are_contained(library, family):
    ref, path, _, attribute = family
    folder = _bad_files(library, ref, path)
    view = library.load()
    codes = {(p.path, p.code) for p in view.problems}
    assert (f"{folder}/broken.json", "malformed") in codes
    assert (f"{folder}/newer.json", "unsupported_format") in codes
    assert (f"{folder}/x-2.json", "address_mismatch") in codes
    assert (f"{folder}/X-3.json", "name_mismatch") in codes
    assert (f"{folder}/notes.txt", "unrecognized") in codes
    assert set(getattr(view, attribute)) == {ref, other(ref, "c2", "x-9")}
    with pytest.raises(RecordNotFound):
        library.read(other(ref, rid="absent"))


def test_campaign_listing_matches_load(library, family):
    ref, path, lister, attribute = family
    folder = _bad_files(library, ref, path)
    view = library.load()
    found = lister(library, "c1")
    assert isinstance(found, CampaignRecords)
    assert dict(found.records) == {k: v for k, v in getattr(view, attribute).items() if k.campaign_id == "c1"}
    assert sorted((p.path, p.code) for p in found.problems) == sorted(
        (p.path, p.code) for p in view.problems if p.path.startswith(folder))
    assert lister(library, "nowhere") == CampaignRecords({}, ())


@pytest.mark.parametrize("name", ["accepted", "wrapups"])
def test_look_alike_folder_and_file_not_folder_are_reported(library, name):
    lister = FAMILIES[name][2]
    _write(library.root, f"campaigns/c1/{name.capitalize()}/x.json", b"{}")
    assert (f"campaigns/c1/{name.capitalize()}", "name_mismatch") in {
        (p.path, p.code) for p in lister(library, "c1").problems}
    _write(library.root, f"campaigns/c4/{name}", b"a file")
    assert (f"campaigns/c4/{name}", "unrecognized") in {(p.path, p.code) for p in lister(library, "c4").problems}


def test_libraries_without_them_load_as_before(library):
    view = library.load()
    assert view.accepted == {} and view.wrapups == {} and view.problems == ()


# -- recovery and rollback ----------------------------------------------------------------------

def _crashed_copy(root: Path, name: str, locker: ProcessLocker) -> Path:
    lib = root / name
    shutil.copytree(root / "lib", lib)
    ops = RootedFileOps(root, plan=[Crash(Target("replace", target=f"{name}/.txn/journal.json"), "after")],
                        record=False)
    with pytest.raises(BaseException):
        with LibraryStore.open(lib, ops=ops, locker=locker) as store:
            _commit(store, (record(ACC), None), (record(WRAP), None))
    return lib


def test_a_journal_naming_them_recovers_or_can_be_discarded(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    locker = ProcessLocker()
    with LibraryStore.create(root / "lib", locker=locker) as store:
        seed(store)
    lib = _crashed_copy(root, "lib-recover", locker)
    locker = ProcessLocker()
    with LibraryStore.open(lib, writable=False, locker=locker) as reader:
        with pytest.raises(PendingRecovery):
            reader.campaign_accepted("c1")
    with LibraryStore.open(lib, locker=locker) as store:
        published = {target for target, _ in store.last_recovery.published}
        assert {FAMILIES["accepted"][1], FAMILIES["wrapups"][1]} <= published
        assert store.read(ACC).record == record(ACC)
    lib = _crashed_copy(root, "lib-discard", ProcessLocker())
    locker = ProcessLocker()
    LibraryStore.discard_journal(lib, locker=locker)  # what a pre-021 build's user runs
    with LibraryStore.open(lib, locker=locker) as store:
        assert store.load().accepted == {}
        assert any("discarded" in path for path in store.conflicts())



def test_a_linked_look_alike_folder_is_reported_as_load_reports_it(library, tmp_path):
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    (library.root / "campaigns" / "c1").mkdir(parents=True, exist_ok=True)
    make_dir_link(library.root / "campaigns" / "c1" / "Accepted", outside)
    entries = {(p.path, p.code) for p in library.load().problems}
    found = {(p.path, p.code) for p in library.campaign_accepted("c1").problems}
    assert ("campaigns/c1/Accepted", "link") in entries
    assert ("campaigns/c1/Accepted", "link") in found
