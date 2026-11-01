"""Knowledge records in the native store (Sprint 020): a create-only family on the 010 transaction.

Covers ``campaigns/<id>/events/<id>.json`` and ``campaigns/<id>/evidence/<family>/<id>.json``:
canonical paths, the ``grimoire.knowledge-record/1`` envelope, create-only puts, the journal's
refusal to replace one, and ``campaign_knowledge``: damage codes, links at every level, strangers
and look-alikes. ``load()`` never reads either tree. All fixtures are synthetic.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from grimoire.core.identity import KNOWLEDGE_FAMILIES, KnowledgeRef
from grimoire.store import (
    ImmutableRevision, InvalidEnvelope, KnowledgeRecord, LibraryStore, LinkRefused, MalformedRecord, ProcessLocker,
    RecoveryFailed, StaleWrite, UnsupportedFormat,
)
from grimoire.store.envelope import EnvelopeError, knowledge_record_document, parse_knowledge_record
from grimoire.store.layout import address_parts, classify, knowledge_parts
from grimoire.store.jsonfmt import render
from grimoire.testing import Crash, RootedFileOps, Target

from store_helpers import OWNER, make_dir_link, owner_handle, seed

EVENT = KnowledgeRef("c1", "event", "e1")
BELIEF = KnowledgeRef("c1", "belief", "b1")
BATCH = KnowledgeRef.batch("c1", 1)


def record(ref=EVENT, payload=None, **extra) -> KnowledgeRecord:
    return KnowledgeRecord(ref, "test.knowledge/1", {"x": 1} if payload is None else payload,
                           owner=OWNER, extra=extra)


@pytest.fixture
def library(tmp_path):
    store = LibraryStore.create(tmp_path / "lib")
    seed(store)
    yield store
    store.close()


def _commit(store, *records):
    txn = store.transaction()
    for item in records:
        txn.put(item, None)
    return txn.commit(owner_handle())


def _write(root: Path, relative: str, content) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content if isinstance(content, bytes) else json.dumps(content).encode())


# -- layout and envelope ----------------------------------------------------------------------

@pytest.mark.parametrize("family", KNOWLEDGE_FAMILIES)
def test_canonical_paths_classify_exactly(family):
    ref = KnowledgeRef.batch("c1", 3) if family == "batch" else KnowledgeRef("c1", family, "x-1")
    parts = knowledge_parts(ref)
    assert parts == address_parts(ref)
    assert parts[:3] == ("campaigns", "c1", "events" if family == "event" else "evidence")
    assert classify(parts) == ("knowledge", ref)


@pytest.mark.parametrize("parts", [
    ("campaigns", "c1", "evidence", "event", "e1.json"),       # an event never lives under evidence/
    ("campaigns", "c1", "evidence", "batch", "1.json"),        # a batch id is ten digits
    ("campaigns", "c1", "evidence", "batch", "0000000000.json"),
    ("campaigns", "c1", "evidence", "perception", "e1.json"),  # perceptions share family "evidence"
    ("campaigns", "c1", "evidence", "Belief", "b1.json"),
    ("campaigns", "c1", "evidence", "belief", "B1.json"),
    ("campaigns", "c1", "Events", "e1.json"),
    ("campaigns", "c1", "events", "e1.txt"),
    ("campaigns", "c1", "events", "x", "e1.json"),
])
def test_look_alike_paths_are_not_records(parts):
    assert classify(parts) is None


def test_the_envelope_is_create_only_and_keeps_unknown_fields():
    document = knowledge_record_document(record(**{"x-note": "kept"}))
    assert document["previous"] is None and document["x-note"] == "kept"
    assert document["format"] == "grimoire.knowledge-record/1" and document["address"] == str(EVENT)
    parsed, previous = parse_knowledge_record(document)
    assert parsed == record(**{"x-note": "kept"}) and previous is None


@pytest.mark.parametrize("change, code", [
    ({"previous": "sha256:" + "0" * 64}, "malformed"),
    ({"address": "campaign:c1#chat/t1"}, "malformed"),
    ({"format": "grimoire.knowledge-record/2"}, "unsupported_format"),
    ({"schema": ""}, "malformed"),
])
def test_strict_parsing_names_the_problem(change, code):
    document = {**knowledge_record_document(record()), **change}
    with pytest.raises(EnvelopeError) as caught:
        parse_knowledge_record(document)
    assert caught.value.code == code


def test_invalid_records_are_refused():
    with pytest.raises(InvalidEnvelope):
        KnowledgeRecord("campaign:c1#event/e1", "s/1", {})  # type: ignore[arg-type]
    with pytest.raises(InvalidEnvelope):
        KnowledgeRecord(EVENT, "s/1", {}, extra={"previous": "x"})


# -- writing ----------------------------------------------------------------------------------

def test_records_commit_together_and_read_back(library):
    result = _commit(library, record(), record(BELIEF), record(BATCH))
    assert set(result.etags) == {"campaigns/c1/events/e1.json", "campaigns/c1/evidence/belief/b1.json",
                                 "campaigns/c1/evidence/batch/0000000001.json"}
    stored = library.read(EVENT)
    assert stored.record == record() and stored.previous is None
    assert stored.etag == result.etags["campaigns/c1/events/e1.json"]


def test_a_knowledge_record_is_create_only(library):
    result = _commit(library, record())
    with pytest.raises(ImmutableRevision):
        library.transaction().put(record(payload={"x": 2}), result.etags["campaigns/c1/events/e1.json"])
    with pytest.raises(StaleWrite) as caught:  # expected=None means "must not exist"
        _commit(library, record(payload={"x": 2}))
    assert caught.value.stale == (("campaigns/c1/events/e1.json", "exists"),)
    assert library.read(EVENT).record == record()


def test_a_newer_format_on_disk_reads_as_unsupported(library):
    _write(library.root, "campaigns/c1/events/e1.json",
           {"format": "grimoire.knowledge-record/2", "address": str(EVENT), "payload": {}})
    with pytest.raises(UnsupportedFormat):
        library.read(EVENT)
    assert library.campaign_knowledge("c1").damaged == {EVENT: "unsupported_format"}
    with pytest.raises(StaleWrite):
        _commit(library, record())


def test_a_journal_that_would_replace_a_knowledge_record_fails_recovery(tmp_path):
    lib = tmp_path / "lib"
    with LibraryStore.create(lib) as store:
        seed(store)
        _commit(store, record())
    target = "campaigns/c1/events/e1.json"
    (lib / ".txn").mkdir(exist_ok=True)
    (lib / ".txn" / "a.new").write_bytes(b"{}")
    journal = {"format": "grimoire.journal/1", "state": "commit", "entries": [
        {"kind": "publish", "target": target, "temp": "a.new", "old": "sha256:" + "1" * 64,
         "new": "sha256:" + "2" * 64}]}
    (lib / ".txn" / "journal.json").write_bytes(render(journal))
    before = (lib / target).read_bytes()
    with pytest.raises(RecoveryFailed):
        LibraryStore.open(lib)
    assert (lib / target).read_bytes() == before


def test_a_crash_after_the_commit_point_recovers_a_knowledge_write(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    locker = ProcessLocker()
    with LibraryStore.create(root / "lib", locker=locker) as store:
        seed(store)
    ops = RootedFileOps(root, plan=[Crash(Target("replace", target="lib/.txn/journal.json"), "after")], record=False)
    with pytest.raises(BaseException):
        with LibraryStore.open(root / "lib", ops=ops, locker=locker) as store:
            _commit(store, record(), record(BATCH))
    with LibraryStore.open(root / "lib", locker=ProcessLocker()) as store:
        assert store.read(EVENT).record == record() and store.read(BATCH).record == record(BATCH)


# -- campaign_knowledge -----------------------------------------------------------------------

def test_scan_reads_both_trees_and_snapshots_every_file(library):
    result = _commit(library, record(), record(BELIEF), record(BATCH))
    scan = library.campaign_knowledge("c1")
    assert set(scan.records) == {EVENT, BELIEF, BATCH} and scan.damaged == {} and scan.problems == ()
    assert scan.entries == tuple(sorted((path, "file", tag) for path, tag in result.etags.items()))
    assert library.campaign_knowledge("c2").records == {}  # another campaign: nothing


def test_an_absent_or_look_alike_campaign_scans_empty(library, tmp_path):
    assert library.campaign_knowledge("nobody") == library.campaign_knowledge("nobody")
    assert library.campaign_knowledge("nobody").entries == ()
    (library.root / "campaigns" / "Zed").mkdir()
    (library.root / "campaigns" / "Zed" / "x.json").write_text("{}")
    scan = library.campaign_knowledge("zed")
    if scan.problems:  # case-insensitive filesystems see the look-alike
        assert [p.code for p in scan.problems] == ["name_mismatch"]
    assert scan.records == {} and scan.entries == ()


def test_damaged_files_get_a_code_and_strangers_are_problems(library):
    _commit(library, record(BATCH))
    root = library.root
    _write(root, "campaigns/c1/events/e1.json", b"{not json")
    _write(root, "campaigns/c1/evidence/belief/b1.json",
           {"format": "grimoire.knowledge-record/1", "address": "campaign:c1#belief/b2", "schema": "s/1",
            "payload": {}, "previous": None})
    _write(root, "campaigns/c1/evidence/ruling/r1.json", {"format": "grimoire.knowledge-record/9"})
    _write(root, "campaigns/c1/evidence/belief/B2.json", {"secret": "never copied"})
    _write(root, "campaigns/c1/evidence/belief/notes.txt", b"secret text")
    _write(root, "campaigns/c1/evidence/unknown/x.json", b"{}")
    _write(root, "campaigns/c1/events/.DS_Store", b"noise")
    (root / "campaigns/c1/evidence/proposition/p1.json").mkdir(parents=True)  # a folder at a file name
    _write(root, "campaigns/c1/evidence/proposition/p1.json/inner.json", b"{}")
    scan = library.campaign_knowledge("c1")
    assert scan.damaged == {
        EVENT: "malformed", BELIEF: "address_mismatch", KnowledgeRef("c1", "ruling", "r1"): "unsupported_format",
        KnowledgeRef("c1", "proposition", "p1"): "malformed"}
    assert set(scan.records) == {BATCH}
    codes = {(p.path, p.code) for p in scan.problems}
    assert codes == {("campaigns/c1/evidence/belief/B2.json", "name_mismatch"),
                     ("campaigns/c1/evidence/belief/notes.txt", "unrecognized"),
                     ("campaigns/c1/evidence/unknown", "unrecognized")}
    assert all("secret" not in p.detail and "never" not in p.detail for p in scan.problems)
    paths = [path for path, _, _ in scan.entries]
    assert "campaigns/c1/evidence/belief/notes.txt" in paths and "campaigns/c1/events/.DS_Store" not in paths


def test_a_look_alike_tree_folder_is_reported(library):
    _write(library.root, "campaigns/c1/EVIDENCE/belief/b1.json", b"{}")
    scan = library.campaign_knowledge("c1")
    assert ("campaigns/c1/EVIDENCE", "name_mismatch") in {(p.path, p.code) for p in scan.problems}


def test_load_never_reads_the_knowledge_trees(library, tmp_path):
    before = library.load()
    _commit(library, record(), record(BELIEF), record(BATCH))
    _write(library.root, "campaigns/c1/evidence/belief/garbage.txt", b"x")
    after = library.load()
    assert after.problems == before.problems and dict(after.instances) == dict(before.instances)
    from grimoire.testing import RootedFileOps as Recording
    with LibraryStore.open(library.root, writable=False, ops=Recording(library.root.parent)) as reader:
        ops = reader._access.ops
        reader.load()
        touched = [op.path for op in ops.trace]
    assert any(path.endswith("campaigns/c1") for path in touched)  # the campaign was listed...
    assert not any("/events" in path or "/evidence" in path for path in touched)  # ...its trees never


def test_a_read_only_store_can_scan(library):
    _commit(library, record())
    with LibraryStore.open(library.root, writable=False) as reader:
        assert set(reader.campaign_knowledge("c1").records) == {EVENT}


# -- links, one level at a time ---------------------------------------------------------------

def _outside(tmp_path: Path) -> Path:
    outside = tmp_path / "outside"
    _write(outside, "e9.json", b"{}")
    _write(outside, "belief/b9.json", b"{}")
    return outside


def _codes(scan):
    return {(p.path, p.code) for p in scan.problems}


def test_a_linked_campaign_folder_is_reported_not_followed(library, tmp_path):
    make_dir_link(library.root / "campaigns" / "linked", _outside(tmp_path))
    scan = library.campaign_knowledge("linked")
    assert ("campaigns/linked", "link") in _codes(scan) and scan.records == {}


def test_a_linked_tree_is_reported_not_followed(library, tmp_path):
    make_dir_link(library.root / "campaigns" / "c1" / "events", _outside(tmp_path))
    scan = library.campaign_knowledge("c1")
    assert ("campaigns/c1/events", "link") in _codes(scan) and scan.records == {}


def test_a_linked_family_folder_is_reported_not_followed(library, tmp_path):
    (library.root / "campaigns" / "c1" / "evidence").mkdir(parents=True)
    make_dir_link(library.root / "campaigns" / "c1" / "evidence" / "belief", _outside(tmp_path) / "belief")
    scan = library.campaign_knowledge("c1")
    assert ("campaigns/c1/evidence/belief", "link") in _codes(scan) and scan.records == {}


def test_a_linked_record_file_is_damage(library, tmp_path):
    outside = _outside(tmp_path)
    (library.root / "campaigns" / "c1" / "events").mkdir(parents=True)
    try:
        (library.root / "campaigns" / "c1" / "events" / "e1.json").symlink_to(outside / "e9.json")
    except (OSError, NotImplementedError):
        pytest.skip("file links are not available here")
    scan = library.campaign_knowledge("c1")
    assert scan.damaged == {EVENT: "link"} and ("campaigns/c1/events/e1.json", "link") in _codes(scan)
    with pytest.raises(LinkRefused):
        library.read(EVENT)


def test_a_linked_campaigns_root_is_refused(tmp_path):
    lib = tmp_path / "lib"
    LibraryStore.create(lib).close()
    shutil.rmtree(lib / "campaigns", ignore_errors=True)
    if (lib / "campaigns").exists():
        (lib / "campaigns").rmdir()
    make_dir_link(lib / "campaigns", _outside(tmp_path))
    with LibraryStore.open(lib, writable=False) as reader:
        with pytest.raises(LinkRefused):
            reader.campaign_knowledge("c1")


def test_errors_never_carry_malformed_content(library):
    _write(library.root, "campaigns/c1/events/e1.json", b'{"format": "SENTINEL-TEXT"}')
    scan = library.campaign_knowledge("c1")
    assert scan.damaged == {EVENT: "unsupported_format"}
    assert "SENTINEL" not in repr(scan)
    with pytest.raises(MalformedRecord):
        library.read(EVENT)
