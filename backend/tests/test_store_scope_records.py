"""Play-scope and chat records in the native store (Sprint 107): the same guarded transaction as 010.

Covers the additive family ``campaigns/<id>/scope.json`` and ``campaigns/<id>/chats/<chat>.json``:
canonical paths, the ``grimoire.scope-record/1`` envelope, CAS, authorization, collisions,
links, containment on load, the per-source ``check_definition`` parity with ``load()`` and the
read-only campaign helpers. All fixtures are synthetic.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from grimoire.core.identity import ChatRef, InstanceRef, ScopeRef, SourceRef
from grimoire.library import LibraryPathError
from grimoire.security import Approval, Authorizer, Grant, Permission, ResourcePath, payload_digest
from grimoire.store import (
    InvalidEnvelope, LibraryStore, LinkRefused, NameCollision, RecordEnvelope, RecordNotFound, ScopeRecord,
    SourceManifest, StaleWrite, UnstorableIdentifier, WriteDenied,
)
from grimoire.store.envelope import EnvelopeError, parse_scope_record, scope_record_document
from grimoire.store.layout import chat_parts, classify, scope_parts
from grimoire.testing.fileops import snapshot

from store_helpers import (
    ADDON, HARBOR, OWNER, WREN, WREN_A, WREN_R1, WREN_R2, harbor_manifest, instance, make_dir_link, owner_handle,
    seed, wren,
)

SCOPE = ScopeRef("quick-1")
CHAT = ChatRef("quick-1", "thread-1")
HARBOR_DIR = Path("sources/world/harbor/_source")


def scope_record(title=None, **extra) -> ScopeRecord:
    return ScopeRecord(SCOPE, "test.scope/1", {"title": title}, owner=OWNER, extra=extra)


def chat_record(label="Mara", ref=CHAT) -> ScopeRecord:
    return ScopeRecord(ref, "test.chat/1", {"label": label})


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

def test_canonical_paths_classify_exactly():
    assert scope_parts(SCOPE) == ("campaigns", "quick-1", "scope.json")
    assert chat_parts(CHAT) == ("campaigns", "quick-1", "chats", "thread-1.json")
    assert classify(scope_parts(SCOPE)) == ("scope", SCOPE)
    assert classify(chat_parts(CHAT)) == ("chat", CHAT)


@pytest.mark.parametrize("parts", [
    ("campaigns", "q", "chats", "x.JSON"), ("campaigns", "q", "chats", "x", "y.json"),
    ("campaigns", "q", "Scope.json"), ("campaigns", "q", "scope.json", "x"), ("campaigns", "q", "chats.json"),
    ("campaigns", "Q", "scope.json"), ("campaigns", "q", "Chats", "x.json"), ("campaigns", "q", "chats", "X.json"),
    ("campaigns", "q", "scope"),
])
def test_look_alike_paths_are_not_records(parts):
    assert classify(parts) is None


def test_a_chat_id_needs_room_for_the_file_suffix():
    assert chat_parts(ChatRef("q", "t" * 123))
    with pytest.raises(UnstorableIdentifier):
        chat_parts(ChatRef("q", "t" * 124))


def test_the_envelope_owns_address_and_schema_and_keeps_unknown_fields():
    record = ScopeRecord(CHAT, "test.chat/1", {"a": [1]}, owner=OWNER, extra={"x-note": {"k": True}})
    document = scope_record_document(record, None)
    assert document["format"] == "grimoire.scope-record/1"
    assert document["address"] == "campaign:quick-1#chat/thread-1"
    parsed, previous = parse_scope_record(document)
    assert parsed == record and previous is None


@pytest.mark.parametrize("kwargs", [
    {"address": InstanceRef("q", "chat", "t"), "schema": "s"}, {"address": CHAT, "schema": ""},
    {"address": CHAT, "schema": "s" * 129}, {"address": CHAT, "schema": "s", "owner": " "},
    {"address": CHAT, "schema": "s", "extra": {"payload": 1}},
])
def test_invalid_scope_records_are_refused(kwargs):
    with pytest.raises(InvalidEnvelope):
        ScopeRecord(**kwargs)


@pytest.mark.parametrize("document, code", [
    ({"format": "grimoire.scope-record/2", "address": "campaign:q", "schema": "s", "payload": {}},
     "unsupported_format"),
    ({"format": "grimoire.scope-record/1", "address": "campaign:q/k/i", "schema": "s", "payload": {}}, "malformed"),
    ({"format": "grimoire.scope-record/1", "address": "campaign:q", "payload": {}}, "malformed"),
    ({"format": "grimoire.scope-record/1", "address": "campaign:q", "schema": "s"}, "malformed"),
    ({"format": "grimoire.scope-record/1", "address": "campaign:q", "schema": "s", "payload": {},
      "previous": "nope"}, "malformed"),
])
def test_strict_parsing_names_the_problem(document, code):
    with pytest.raises(EnvelopeError) as error:
        parse_scope_record(document)
    assert error.value.code == code


# -- the guarded transaction ------------------------------------------------------------------

def test_scope_chat_and_instance_commit_together_and_read_back(library):
    actor = InstanceRef("quick-1", "character", "wren-1")
    result = _commit(library, (scope_record(), None), (chat_record(), None), (instance(actor, WREN_R1), None))
    assert set(result.etags) == {"campaigns/quick-1/scope.json", "campaigns/quick-1/chats/thread-1.json",
                                 "campaigns/quick-1/instances/character/wren-1.json"}
    stored = library.read(CHAT)
    assert stored.record == chat_record() and stored.previous is None
    assert stored.etag == result.etags["campaigns/quick-1/chats/thread-1.json"]
    view = library.load()
    assert view.problems == ()
    assert set(view.scopes) == {SCOPE} and set(view.chats) == {CHAT}
    assert view.scopes[SCOPE].record.extra == {}
    assert actor in view.instances


def test_compare_and_swap_covers_the_new_family(library):
    first = _commit(library, (chat_record(), None)).etags["campaigns/quick-1/chats/thread-1.json"]
    with pytest.raises(StaleWrite, match="exists"):
        _commit(library, (chat_record("again"), None))
    second = _commit(library, (chat_record("renamed"), first)).etags["campaigns/quick-1/chats/thread-1.json"]
    assert library.read(CHAT).previous == first
    with pytest.raises(StaleWrite, match="changed"):
        _commit(library, (chat_record("lost"), first))
    with pytest.raises(StaleWrite, match="absent"):
        _commit(library, (scope_record(), second))
    assert library.read(CHAT).record.payload == {"label": "renamed"}


def test_a_newer_scope_record_format_is_never_overwritten(library):
    newer = {"format": "grimoire.scope-record/2", "address": str(CHAT), "schema": "s", "payload": {}}
    _write(library.root, "campaigns/quick-1/chats/thread-1.json", newer)
    data = (library.root / "campaigns/quick-1/chats/thread-1.json").read_bytes()
    from grimoire.store import etag
    with pytest.raises(StaleWrite, match="unsupported_format"):
        _commit(library, (chat_record(), etag(data)))
    assert (library.root / "campaigns/quick-1/chats/thread-1.json").read_bytes() == data


def test_writes_are_authorized_per_path_and_denial_writes_nothing(library):
    before = snapshot(library.root)
    with pytest.raises(WriteDenied):
        _commit(library, (scope_record(), None), (chat_record(), None), handle=owner_handle("sources"))
    with pytest.raises(WriteDenied):  # a grant on another campaign does not cover this one
        _commit(library, (chat_record(), None), handle=owner_handle("campaigns/other"))
    assert snapshot(library.root) == before
    _commit(library, (chat_record(), None), handle=owner_handle("campaigns/quick-1/chats"))


def test_an_approval_gated_chat_write_commits_once_approved(library):
    authority = Authorizer([OWNER])
    authority.add_grant(Grant("g", "app:chat", frozenset({Permission.APPLY}), ResourcePath.parse("campaigns"), OWNER))
    txn = library.transaction()
    address = txn.put(chat_record(), None)
    with pytest.raises(WriteDenied):
        library_txn = library.transaction()
        library_txn.put(chat_record(), None)
        library_txn.commit(authority.handle_for("app:chat"))
    authority.register_approval(Approval("a1", "app:chat", Permission.APPLY, ResourcePath.parse(address),
                                         payload_digest(txn.preview()[address]), OWNER))
    txn.commit(authority.handle_for("app:chat"), {address: "a1"})
    assert library.read(CHAT).record == chat_record()


@pytest.mark.parametrize("existing, record", [
    ("campaigns/quick-1/Chats/thread-1.json", chat_record()),
    ("campaigns/quick-1/chats/Thread-1.json", chat_record()),
    ("campaigns/quick-1/Scope.json", scope_record()),
])
def test_a_differently_spelled_existing_entry_is_a_collision(library, existing, record):
    _write(library.root, existing, b"{}")
    with pytest.raises(NameCollision):
        _commit(library, (record, None))


def _v1_like(path: Path) -> Path:
    (path / "campaigns" / "c1" / "instances").mkdir(parents=True)
    (path / "worlds").mkdir()
    (path / "regex.json").write_text("[]")
    return path


def test_a_linked_chats_folder_is_never_written_or_followed(library, tmp_path):
    v1 = _v1_like(tmp_path / "old")
    (library.root / "campaigns" / "quick-1").mkdir(parents=True)
    make_dir_link(library.root / "campaigns" / "quick-1" / "chats", v1 / "campaigns")
    before = snapshot(v1)
    with pytest.raises(LibraryPathError):  # LinkRefused, or V1 isolation where the link resolves
        _commit(library, (chat_record(), None))
    view = library.load()
    assert ("campaigns/quick-1/chats", "link") in {(p.path, p.code) for p in view.problems}
    assert view.chats == {}
    with pytest.raises(LinkRefused):
        library.read(CHAT)
    assert snapshot(v1) == before


def test_a_linked_scope_file_is_never_written_or_read(library, tmp_path):
    v1 = _v1_like(tmp_path / "old")
    (library.root / "campaigns" / "quick-1").mkdir(parents=True)
    try:
        (library.root / "campaigns" / "quick-1" / "scope.json").symlink_to(v1 / "regex.json")
    except (OSError, NotImplementedError):
        pytest.skip("file links are not available here")
    before = snapshot(v1)
    with pytest.raises(LibraryPathError):
        _commit(library, (scope_record(), None))
    view = library.load()
    assert ("campaigns/quick-1/scope.json", "link") in {(p.path, p.code) for p in view.problems}
    assert view.scopes == {}
    assert snapshot(v1) == before


# -- loading --------------------------------------------------------------------------------

def test_bad_files_are_contained_and_strangers_are_reported(library):
    _commit(library, (scope_record(), None), (chat_record(), None))
    root = library.root
    other = ChatRef("quick-1", "thread-2")
    _write(root, "campaigns/quick-1/chats/broken.json", b"{not json")
    _write(root, "campaigns/quick-1/chats/newer.json",
           {"format": "grimoire.scope-record/9", "address": "campaign:quick-1#chat/newer", "schema": "s",
            "payload": {}})
    copied = json.loads((root / "campaigns/quick-1/chats/thread-1.json").read_bytes())
    _write(root, "campaigns/quick-1/chats/thread-2.json", copied)  # claims thread-1
    _write(root, "campaigns/quick-1/chats/sub/x.json", b"{}")
    _write(root, "campaigns/other/chats", b"a file, not a folder")
    _write(root, "campaigns/other/scope.json/inside.json", b"{}")
    view = library.load()
    codes = {(p.path, p.code) for p in view.problems}
    assert ("campaigns/quick-1/chats/broken.json", "malformed") in codes
    assert ("campaigns/quick-1/chats/newer.json", "unsupported_format") in codes
    assert ("campaigns/quick-1/chats/thread-2.json", "address_mismatch") in codes
    assert ("campaigns/quick-1/chats/sub", "unrecognized") in codes
    assert ("campaigns/other/chats", "unrecognized") in codes
    assert ("campaigns/other/scope.json", "malformed") in codes
    assert set(view.chats) == {CHAT} and other not in view.chats
    assert set(view.scopes) == {SCOPE}
    with pytest.raises(RecordNotFound):
        library.read(ChatRef("quick-1", "absent"))


def test_libraries_without_the_new_family_load_exactly_as_before(library):
    view = library.load()
    assert view.scopes == {} and view.chats == {} and view.problems == ()


# -- read-only helpers --------------------------------------------------------------------------

def test_campaign_presence_is_decided_from_listings(library):
    assert library.campaign_presence("c1") == "present"
    assert library.campaign_presence("quick-1") == "absent"
    (library.root / "campaigns" / "Quick-1").mkdir()
    assert library.campaign_presence("quick-1") == "mismatch"
    with pytest.raises(ValueError):
        library.campaign_presence("Bad")


def test_campaign_instances_lists_names_across_kinds(library):
    _commit(library, (RecordEnvelope(InstanceRef("c1", "item", "lamp"), {}), None))
    _write(library.root, "campaigns/c1/instances/character/Odd.json", b"{}")
    _write(library.root, "campaigns/c1/instances/character/notes.txt", b"x")
    assert library.campaign_instances("c1") == (InstanceRef("c1", "character", "wren-a"),
                                                InstanceRef("c1", "item", "lamp"))
    assert library.campaign_instances("absent") == ()


def test_check_source_reports_why_a_source_is_not_admitted(library):
    assert library.check_source(HARBOR).manifest.source == HARBOR
    assert library.check_source("catalog:nothing") == "missing_source"
    _write(library.root, "sources/addon/fan/_source/manifest.json", b"{broken")
    assert library.check_source(ADDON) == "excluded_source"


def _parity(library, ref: SourceRef) -> tuple[object, object]:
    """``check_definition``'s verdict and ``load()``'s, in the same vocabulary."""
    found = library.check_definition(ref)
    mine = found if isinstance(found, str) else found.ref
    view = library.load()
    if ref.source in view.excluded:
        theirs = "excluded_source"
    elif ref.unpinned() in view.dropped:
        theirs = "entity_dropped"
    else:
        broken = view.index.check(ref)
        theirs = broken.reason if broken is not None else view.index.resolve(ref).ref
    return mine, theirs


def _hand_edit(library, case: str) -> None:
    root = library.root
    record = json.loads((root / HARBOR_DIR / "records/character/wren/r1.json").read_bytes())
    if case == "bad_head":
        manifest = json.loads((root / HARBOR_DIR / "manifest.json").read_bytes())
        manifest["heads"] = {"character/wren": "r7"}
        _write(root, str(HARBOR_DIR / "manifest.json"), manifest)
    elif case == "bad_head_key":
        manifest = json.loads((root / HARBOR_DIR / "manifest.json").read_bytes())
        manifest["heads"] = {"no-slash": "r1"}
        _write(root, str(HARBOR_DIR / "manifest.json"), manifest)
    elif case == "kind_reuse":
        item = SourceRef(HARBOR, "item", "wren", "r1")
        _write(root, str(HARBOR_DIR / "records/item/wren/r1.json"),
               {"format": "grimoire.record/1", "address": str(item), "payload": {}})
    elif case == "missing_head":
        record["address"] = str(WREN_R2)
        _write(root, str(HARBOR_DIR / "records/character/wren/r2.json"), record)
    elif case == "unreadable_revision":
        _write(root, str(HARBOR_DIR / "records/character/wren/r1.json"), b"{not json")
    elif case == "ownership":
        record["address"] = str(SourceRef(HARBOR, "character", "intruder", "r1"))
        _write(root, "sources/world/harbor/_source/records/character/intruder/r1.json", record)
        record["address"] = str(SourceRef(ADDON, "character", "x", "r1"))
        _write(root, str(HARBOR_DIR / "records/character/x/r1.json"), record)
    elif case == "records_without_manifest":
        (root / HARBOR_DIR / "manifest.json").unlink()


@pytest.mark.parametrize("case", ["clean", "bad_head", "bad_head_key", "kind_reuse", "missing_head",
                                  "unreadable_revision", "ownership", "records_without_manifest"])
@pytest.mark.parametrize("ref", [WREN, WREN_R1, WREN_R2, SourceRef(HARBOR, "character", "ghost"),
                                 SourceRef("catalog:none", "character", "x")])
def test_check_definition_agrees_with_load(library, case, ref):
    _hand_edit(library, case)
    mine, theirs = _parity(library, ref)
    assert mine == theirs


def test_an_implicit_single_revision_head_resolves(library):
    assert library.read_source(HARBOR).manifest.heads == {}
    assert library.check_definition(WREN).ref == WREN_R1


def test_a_journal_naming_chat_targets_recovers_or_can_be_discarded(tmp_path):
    from grimoire.store import ProcessLocker
    from grimoire.testing import Crash, RootedFileOps, Target
    root = tmp_path / "root"
    root.mkdir()
    locker = ProcessLocker()
    with LibraryStore.create(root / "lib", locker=locker) as store:
        seed(store)
    for attempt, finish in enumerate(("recover", "discard")):
        lib = root / f"lib{attempt}"
        import shutil
        shutil.copytree(root / "lib", lib)
        ops = RootedFileOps(root, plan=[Crash(Target("replace", target=f"lib{attempt}/.txn/journal.json"),
                                              "after")], record=False)
        with pytest.raises(BaseException):
            with LibraryStore.open(lib, ops=ops, locker=locker) as store:
                _commit(store, (scope_record(), None), (chat_record(), None))
        locker = ProcessLocker()
        if finish == "recover":
            with LibraryStore.open(lib, locker=locker) as store:
                assert set(store.last_recovery.published) and store.read(CHAT).record == chat_record()
        else:
            LibraryStore.discard_journal(lib, locker=locker)
            with LibraryStore.open(lib, locker=locker) as store:
                assert store.load().chats == {}
                assert any("discarded" in path for path in store.conflicts())
