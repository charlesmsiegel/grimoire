"""Counted-holdings records in the native store (Sprint 023): the same guarded transaction as 010/107.

Covers the additive family ``campaigns/<id>/holdings/<kind>/<instance_id>.json`` on the
``grimoire.scope-record/1`` envelope: canonical paths, explicit address dispatch, CAS, authorization,
collisions, links, containment on load and journal recovery. All fixtures are synthetic.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from grimoire.core.identity import AcceptedRef, ChatRef, HoldingsRef, InstanceRef, RelationRef, ScopeRef, WrapUpRef
from grimoire.library import LibraryPathError
from grimoire.store import (
    LibraryStore, LinkRefused, NameCollision, ProcessLocker, RecordNotFound, RecoveryFailed, ScopeRecord,
    StaleWrite, UnstorableIdentifier, WriteDenied, etag,
)
from grimoire.store.envelope import EnvelopeError, parse_scope_record, scope_record_document
from grimoire.store.layout import classify, holdings_parts
from grimoire.testing import Crash, RootedFileOps, Target
from grimoire.testing.fileops import snapshot

from store_helpers import OWNER, WREN_A, WREN_R1, instance, make_dir_link, owner_handle, seed

MARA = HoldingsRef("c1", "character", "mara-1")
PATH = "campaigns/c1/holdings/character/mara-1.json"


def holdings_record(count: int = 1, ref: HoldingsRef = MARA, **extra) -> ScopeRecord:
    return ScopeRecord(ref, "test.holdings/1", {"count": count}, owner=OWNER, extra=extra)


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


# -- layout and envelope ----------------------------------------------------------------------

def test_holdings_paths_classify_exactly():
    assert holdings_parts(MARA) == ("campaigns", "c1", "holdings", "character", "mara-1.json")
    assert classify(holdings_parts(MARA)) == ("holdings", MARA)


@pytest.mark.parametrize("parts", [
    ("campaigns", "c1", "holdings", "x.json"), ("campaigns", "c1", "holdings", "character", "Mara.json"),
    ("campaigns", "c1", "holdings", "character", "m", "extra.json"), ("campaigns", "c1", "Holdings", "k", "m.json"),
    ("campaigns", "c1", "holdings", "Kind", "m.json"), ("campaigns", "c1", "holdings", "k", "m.JSON"),
    ("campaigns", "c1", "holdings.json"),
])
def test_look_alike_holdings_paths_are_not_records(parts):
    assert classify(parts) is None


def test_an_instance_id_needs_room_for_the_file_suffix():
    assert holdings_parts(HoldingsRef("c", "k", "i" * 123))
    with pytest.raises(UnstorableIdentifier):
        holdings_parts(HoldingsRef("c", "k", "i" * 124))


def test_scope_record_dispatch_is_explicit():
    for address in (ScopeRef("c1"), ChatRef("c1", "t1"), RelationRef("c1", "r-1"), AcceptedRef("c1", "holdings"),
                    WrapUpRef("c1", "holdings"), MARA):  # an id spelled "holdings" never routes to 023
        record = ScopeRecord(address, "s", {})
        parsed, _ = parse_scope_record(scope_record_document(record, None))
        assert parsed == record and type(parsed.address) is type(address)
    for text in ("campaign:x#other/y", "campaign:x/k/i", "campaign:x/k/i#holding", "campaign:x#holdings",
                 "campaign:x/k/i#accepted/e", "campaign:x#accepted/e#holdings", "campaign:x#wrapup/w/holdings"):
        document = {"format": "grimoire.scope-record/1", "address": text, "schema": "s", "payload": {}}
        with pytest.raises(EnvelopeError) as error:
            parse_scope_record(document)
        assert error.value.code == "malformed"
    for value in ({"diary": "a private line"}, ["campaign:x"], 7, None, ["x" * 1_000_000]):  # never echoed
        document = {"format": "grimoire.scope-record/1", "address": value, "schema": "s", "payload": {}}
        with pytest.raises(EnvelopeError) as error:
            parse_scope_record(document)
        assert error.value.code == "malformed" and len(error.value.detail) < 100
        assert "private" not in error.value.detail and "must be text" in error.value.detail
    document = {"format": "grimoire.scope-record/1", "address": "campaign:c#relation/" + "r" * 130, "schema": "s",
                "payload": {}}
    with pytest.raises(EnvelopeError) as error:
        parse_scope_record(document)
    assert "longer than" in error.value.detail  # the family's own reason, as before 023


# -- the guarded transaction ------------------------------------------------------------------

def test_holdings_commit_with_instances_and_read_back(library):
    actor = InstanceRef("c1", "character", "mara-1")
    result = _commit(library, (holdings_record(x_note=[1]), None), (instance(actor, WREN_R1), None))
    assert PATH in result.etags
    stored = library.read(MARA)
    assert stored.record == holdings_record(x_note=[1]) and stored.previous is None
    assert set(library.campaign_holdings("c1").records) == {MARA}


def test_cas_and_newer_format_cover_holdings(library):
    first = _commit(library, (holdings_record(1), None)).etags[PATH]
    with pytest.raises(StaleWrite, match="exists"):
        _commit(library, (holdings_record(2), None))
    _commit(library, (holdings_record(2), first))
    with pytest.raises(StaleWrite, match="changed"):
        _commit(library, (holdings_record(3), first))
    newer = {"format": "grimoire.scope-record/2", "address": str(MARA), "schema": "s", "payload": {}}
    _write(library.root, PATH, newer)
    data = (library.root / PATH).read_bytes()
    with pytest.raises(StaleWrite, match="unsupported_format"):
        _commit(library, (holdings_record(4), etag(data)))
    assert (library.root / PATH).read_bytes() == data


def test_holdings_writes_are_authorized_per_path(library):
    before = snapshot(library.root)
    with pytest.raises(WriteDenied):
        _commit(library, (holdings_record(), None), handle=owner_handle("campaigns/c1/instances"))
    with pytest.raises(WriteDenied):
        _commit(library, (holdings_record(), None), handle=owner_handle("campaigns/other"))
    assert snapshot(library.root) == before
    _commit(library, (holdings_record(), None), handle=owner_handle("campaigns/c1/holdings"))


@pytest.mark.parametrize("existing", [
    "campaigns/c1/Holdings/character/mara-1.json", "campaigns/c1/holdings/Character/mara-1.json",
    "campaigns/c1/holdings/character/Mara-1.json",
])
def test_a_differently_spelled_holdings_entry_is_a_collision(library, existing):
    _write(library.root, existing, b"{}")
    with pytest.raises(NameCollision):
        _commit(library, (holdings_record(), None))


def test_a_linked_holdings_folder_is_never_written_or_followed(library, tmp_path):
    v1 = tmp_path / "old"
    (v1 / "campaigns" / "c1" / "instances").mkdir(parents=True)
    (v1 / "worlds").mkdir()
    (v1 / "regex.json").write_text("[]")
    make_dir_link(library.root / "campaigns" / "c1" / "holdings", v1 / "campaigns")
    before = snapshot(v1)
    with pytest.raises(LibraryPathError):  # LinkRefused, or V1 isolation where the link resolves
        _commit(library, (holdings_record(), None))
    found = library.campaign_holdings("c1")
    assert ("campaigns/c1/holdings", "link") in {(p.path, p.code) for p in found.problems}
    assert found.records == {}
    with pytest.raises(LinkRefused):
        library.read(MARA)
    assert snapshot(v1) == before


# -- loading --------------------------------------------------------------------------------

def test_bad_holdings_files_are_contained(library):
    _commit(library, (holdings_record(), None))
    root = library.root
    _write(root, "campaigns/c1/holdings/character/broken.json", b"{not json")
    copied = json.loads((root / PATH).read_bytes())
    _write(root, "campaigns/c1/holdings/character/toma-1.json", copied)  # claims mara-1
    _write(root, "campaigns/c1/holdings/notes.txt", b"hello")
    _write(root, "campaigns/c1/holdings/character/sub/x.json", b"{}")
    _write(root, "campaigns/c2/holdings", b"a file, not a folder")
    found = library.campaign_holdings("c1")
    codes = {(p.path, p.code) for p in found.problems}
    assert ("campaigns/c1/holdings/character/broken.json", "malformed") in codes
    assert ("campaigns/c1/holdings/character/toma-1.json", "address_mismatch") in codes
    assert ("campaigns/c1/holdings/notes.txt", "unrecognized") in codes
    assert ("campaigns/c1/holdings/character/sub", "unrecognized") in codes
    assert ("campaigns/c2/holdings", "unrecognized") in {(p.path, p.code) for p in library.campaign_holdings("c2").problems}
    assert set(found.records) == {MARA}
    view = library.load()  # holdings are history, like 020's trees: load() skips the folder entirely
    assert not [p for p in view.problems if "holdings" in p.path] and WREN_A in view.instances
    with pytest.raises(RecordNotFound):
        library.read(HoldingsRef("c1", "character", "absent"))


def test_relation_knowledge_image_effect_and_holdings_records_live_side_by_side(library):
    from grimoire.core.identity import KnowledgeRef
    from grimoire.store import KnowledgeRecord
    relation = ScopeRecord(RelationRef("c1", "r-1"), "test.relation/1", {"note": "a"}, owner=OWNER)
    event = KnowledgeRecord(KnowledgeRef("c1", "event", "e-1"), "test.event/1", {"note": "b"}, owner=OWNER)
    from grimoire.core.identity import ImageRef, PlacementRef
    from grimoire.store import AssetRecord
    image = AssetRecord(ImageRef("campaign:c1", "img-1"), "test.image/1", {"note": "c"}, owner=OWNER)
    placement = AssetRecord(PlacementRef(MARA.holder, "avatar"), "test.placement/1", {"note": "d"}, owner=OWNER)
    accepted = ScopeRecord(AcceptedRef("c1", "e-1"), "test.effect/1", {"note": "e"}, owner=OWNER)
    wrapup = ScopeRecord(WrapUpRef("c1", "w-1"), "test.effect/1", {"note": "f"}, owner=OWNER)
    _commit(library, (holdings_record(), None), (relation, None), (event, None), (image, None), (placement, None),
            (accepted, None), (wrapup, None))
    assert library.read(AcceptedRef("c1", "e-1")).record == accepted
    assert library.read(WrapUpRef("c1", "w-1")).record == wrapup
    assert set(library.campaign_accepted("c1").records) == {AcceptedRef("c1", "e-1")}
    assert set(library.campaign_wrapups("c1").records) == {WrapUpRef("c1", "w-1")}
    assert library.read(ImageRef("campaign:c1", "img-1")).record == image
    assert library.read(PlacementRef(MARA.holder, "avatar")).record == placement
    view = library.load()
    assert set(view.relations) == {RelationRef("c1", "r-1")} and view.problems == ()
    assert library.read(MARA).record == holdings_record()
    assert library.read(RelationRef("c1", "r-1")).record == relation
    assert library.read(KnowledgeRef("c1", "event", "e-1")).record == event
    assert set(library.campaign_holdings("c1").records) == {MARA}


def test_campaign_holdings_reads_one_campaign_with_per_file_containment(library):
    other = HoldingsRef("c2", "character", "ivo-1")
    _commit(library, (holdings_record(), None), (holdings_record(ref=other), None))
    root = library.root
    _write(root, "campaigns/c1/holdings/character/broken.json", b"{not json")
    _write(root, "campaigns/c1/holdings/notes.txt", b"x")
    _write(root, "campaigns/c2/holdings/character/bad.json", b"{not json")
    # a look-alike in a campaign with no real holdings folder, so case-insensitive filesystems keep it apart
    _write(root, "campaigns/c3/Holdings/character/x.json", b"{}")
    found = library.campaign_holdings("c1")
    assert set(found.records) == {MARA} and found.records[MARA].record == holdings_record()
    assert {(p.path, p.code) for p in found.problems} == {
        ("campaigns/c1/holdings/character/broken.json", "malformed"),
        ("campaigns/c1/holdings/notes.txt", "unrecognized"),
    }
    look_alike = library.campaign_holdings("c3")
    assert look_alike.records == {}
    assert {(p.path, p.code) for p in look_alike.problems} == {("campaigns/c3/Holdings", "name_mismatch")}
    assert set(library.campaign_holdings("c2").records) == {other}
    assert library.campaign_holdings("absent").records == {}


def test_a_linked_look_alike_holdings_folder_is_reported_as_a_link(library, tmp_path):
    (tmp_path / "elsewhere").mkdir()
    (library.root / "campaigns" / "c1").mkdir(parents=True, exist_ok=True)
    make_dir_link(library.root / "campaigns" / "c1" / "Holdings", tmp_path / "elsewhere")
    found = library.campaign_holdings("c1")
    codes = {(p.path, p.code) for p in found.problems}
    assert ("campaigns/c1/Holdings", "link") in codes and found.records == {}
    # a case-insensitive filesystem also finds the link under the canonical spelling: still only links
    assert codes <= {("campaigns/c1/Holdings", "link"), ("campaigns/c1/holdings", "link")}


def test_an_unreadable_campaign_folder_is_contained(library, monkeypatch):
    _commit(library, (holdings_record(), None))
    access = library._access
    real = access.list_dir

    def failing(parts):
        if tuple(parts) == ("campaigns", "c1"):
            raise PermissionError("denied")
        return real(parts)

    monkeypatch.setattr(access, "list_dir", failing)
    found = library.campaign_holdings("c1")
    assert ("campaigns/c1", "unreadable") in {(p.path, p.code) for p in found.problems}


def test_a_look_alike_kind_folder_is_reported_once(library):
    _write(library.root, "campaigns/c1/holdings/Character/mara-1.json", b"{}")
    problems = library.campaign_holdings("c1").problems
    assert [p.path for p in problems] == ["campaigns/c1/holdings/Character"]  # the folder, not each file in it


def test_a_linked_campaign_folder_is_a_link_problem(library, tmp_path):
    (tmp_path / "elsewhere").mkdir()
    make_dir_link(library.root / "campaigns" / "c9", tmp_path / "elsewhere")
    found = library.campaign_holdings("c9")
    assert found.records == {} and {(p.path, p.code) for p in found.problems} == {("campaigns/c9", "link")}


def test_address_error_details_are_bounded(library):
    document = {"format": "grimoire.scope-record/1", "address": "campaign:x#" + "y" * 5000, "schema": "s",
                "payload": {}}
    with pytest.raises(EnvelopeError) as error:
        parse_scope_record(document)
    assert len(error.value.detail) <= 220


def test_load_never_reads_holdings(library):
    """Holdings logs are history: like 020's events/evidence, load() skips the folder, so its cost never grows."""
    view = library.load()
    assert view.problems == ()
    _commit(library, (holdings_record(), None))
    _write(library.root, "campaigns/c1/holdings/character/broken.json", b"{not json")
    assert library.load().problems == view.problems and not hasattr(library.load(), "holdings")


# -- journal recovery and rollback ------------------------------------------------------------

def _crashed_after_the_journal(root: Path, name: str, locker) -> Path:
    lib = root / name
    shutil.copytree(root / "lib", lib)
    ops = RootedFileOps(root, plan=[Crash(Target("replace", target=f"{name}/.txn/journal.json"), "after")],
                        record=False)
    with pytest.raises(BaseException):
        with LibraryStore.open(lib, ops=ops, locker=locker) as store:
            _commit(store, (holdings_record(), None))
    return lib


@pytest.fixture
def seeded_root(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    with LibraryStore.create(root / "lib", locker=ProcessLocker()) as store:
        seed(store)
    return root


def test_a_journal_naming_holdings_targets_recovers_or_can_be_discarded(seeded_root):
    lib = _crashed_after_the_journal(seeded_root, "lib-recover", ProcessLocker())
    with LibraryStore.open(lib, locker=ProcessLocker()) as store:
        assert store.last_recovery.published and store.read(MARA).record == holdings_record()
    lib = _crashed_after_the_journal(seeded_root, "lib-discard", ProcessLocker())
    LibraryStore.discard_journal(lib, locker=ProcessLocker())
    with LibraryStore.open(lib, locker=ProcessLocker()) as store:
        assert store.campaign_holdings("c1").records == {}
        assert any("discarded" in path for path in store.conflicts())


def test_a_pre_023_build_cannot_recover_a_holdings_journal(seeded_root, monkeypatch):
    lib = _crashed_after_the_journal(seeded_root, "lib-old", ProcessLocker())
    import grimoire.store.journal as journal
    real = journal.classify

    def pre_023(parts):  # an older build does not know the holdings family
        found = real(parts)
        return None if found is not None and found[0] == "holdings" else found

    monkeypatch.setattr(journal, "classify", pre_023)
    with pytest.raises(RecoveryFailed):
        LibraryStore.open(lib, locker=ProcessLocker())
    LibraryStore.discard_journal(lib, locker=ProcessLocker())  # the documented way out
    monkeypatch.undo()
    with LibraryStore.open(lib, locker=ProcessLocker()) as store:
        assert store.campaign_holdings("c1").records == {}
