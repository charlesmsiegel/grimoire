"""Refusals, damage containment and the repair path of the relation service (Sprint 017)."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from grimoire.core import DIRECTOR, AuthorityError, InstanceRef, Moment
from grimoire.core.identity import RelationRef
from grimoire.graphstore import InvalidRelation, relation_endpoint_ids
from grimoire.store import LibraryStore, MalformedRecord, NameCollision, StaleWrite, StoreError, WriteDenied
from grimoire.testing.fileops import snapshot

from graph_fixtures import C, GUILD, HARBOR, INES, LANTERN, TOMAS, WREN, actor, relation_library, seed_campaign
from purity import NETWORK_MODULES
from store_helpers import owner_handle

SRC = Path(__file__).resolve().parents[1] / "src"


@pytest.fixture
def store(tmp_path):
    with LibraryStore.create(tmp_path / "lib") as opened:
        seed_campaign(opened)
        yield opened


@pytest.fixture
def library(store):
    return relation_library(store)


def add(library, predicate="member_of", subject=WREN, obj=GUILD, since=3, until=None, campaign=C):
    return library.add(campaign, predicate, subject, obj, since=Moment(since), provenance="director",
                       until=None if until is None else Moment(until))


def reason(call) -> str:
    with pytest.raises(InvalidRelation) as caught:
        call()
    return caught.value.reason


def write(root: Path, relative: str, content) -> None:
    target = root / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(content if isinstance(content, bytes) else json.dumps(content).encode())


def relation_file(predicate="mentors", subject=WREN, obj=GUILD, rid="r-9", **payload) -> dict:
    return {"format": "grimoire.scope-record/1", "address": f"campaign:{C}#relation/{rid}",
            "schema": "grimoire.relation/0",
            "payload": {"predicate": predicate, "subject": str(subject), "object": str(obj), "since": {"sequence": 1},
                        "provenance": "hand", "recorded_at": "t", **payload}}


# -- add ----------------------------------------------------------------------------------------

@pytest.mark.parametrize("call, expected", [
    (lambda lib: add(lib, predicate="loves"), "unknown_predicate"),
    (lambda lib: add(lib, obj=InstanceRef("other", "organization", "ashen-guild")), "campaign_mismatch"),
    (lambda lib: add(lib, obj=WREN), "self_relation"),
    (lambda lib: add(lib, obj=actor("nobody")), "missing_endpoint"),
    (lambda lib: add(lib, obj=actor("ashen-guild")), "missing_endpoint"),  # same id, another kind: no rebinding
    (lambda lib: add(lib, campaign="absent", subject=InstanceRef("absent", "character", "a"),
                     obj=InstanceRef("absent", "character", "b")), "missing_campaign"),
    (lambda lib: add(lib, until=3), "invalid_until"),
    (lambda lib: add(lib, subject="campaign:c/character/wren"), "invalid_field"),
])
def test_add_refusals(library, call, expected):
    assert reason(lambda: call(library)) == expected


def test_a_look_alike_campaign_folder_is_not_the_campaign(store, library):
    (store.root / "campaigns" / "C2").mkdir(parents=True)
    assert reason(lambda: add(library, campaign="c2", subject=InstanceRef("c2", "character", "a"),
                              obj=InstanceRef("c2", "character", "b"))) == "missing_campaign"


def test_duplicates_are_refused_including_the_reversed_symmetric_form(library):
    add(library).commit()
    assert reason(lambda: add(library, since=5)) == "duplicate_relation"
    add(library, "kin_of", INES, TOMAS, since=0).commit()
    assert reason(lambda: add(library, "kin_of", TOMAS, INES, since=4)) == "duplicate_relation"
    add(library, "member_of", GUILD, WREN, since=3).commit()  # the reversed directed form is another fact


def test_past_stint_before_open_relation(library):
    add(library, subject=TOMAS, since=5).commit()
    past = add(library, subject=TOMAS, since=1, until=3).commit().relation
    relation, _ = library.read(past)
    assert relation.ending.until == Moment(3) and relation.ending.provenance == relation.provenance
    assert relation.ending.at == relation.recorded_at
    assert reason(lambda: add(library, subject=TOMAS, since=2, until=6)) == "duplicate_relation"


def test_endpoint_deleted_between_preview_and_commit_is_stale(store, library):
    change = add(library)
    (store.root / "campaigns/c/instances/organization/ashen-guild.json").unlink()
    before = snapshot(store.root)
    with pytest.raises(StaleWrite):
        change.commit()
    assert snapshot(store.root) == before


def test_two_previewed_adds_second_is_stale(store, library):
    first, second = add(library), add(library, since=4)
    assert first.relation != second.relation
    first.commit()
    with pytest.raises(StaleWrite):
        second.commit()
    assert sorted(p.name for p in (store.root / "campaigns/c/relations").iterdir()) == ["r-1.json"]
    with pytest.raises(StoreError):
        first.commit()  # a change commits once


def test_id_retry_and_exhaustion(store, library):
    write(store.root, "campaigns/c/relations/r-1.json", b"{broken")  # taken, even unreadable
    assert add(library).relation == RelationRef(C, "r-2")
    stuck = relation_library(store)
    stuck._new_id = lambda prefix: "r-1"
    with pytest.raises(StoreError):
        add(stuck, subject=TOMAS)


def test_allocated_id_lookalike_is_name_collision(store, library):
    write(store.root, "campaigns/c/relations/R-1.json", b"{}")
    change = add(library)
    assert change.relation == RelationRef(C, "r-1")  # exact presence decides allocation
    with pytest.raises(NameCollision):
        change.commit()


def test_denied_write_writes_nothing(store):
    library = relation_library(store, handle=owner_handle("campaigns/c/chats"))
    before = snapshot(store.root)
    with pytest.raises(WriteDenied):
        add(library).commit()
    assert snapshot(store.root) == before


# -- end and retract ------------------------------------------------------------------------------

def test_end_retract_cas_and_order(library):
    ref = add(library).commit().relation
    _, etag = library.read(ref)
    with pytest.raises(StaleWrite):
        library.end(ref, until=Moment(9), provenance="d", expected="sha256:" + "0" * 64)
    library.end(ref, until=Moment(9), provenance="d", expected=etag).commit()
    _, etag = library.read(ref)
    assert reason(lambda: library.end(ref, until=Moment(12), provenance="d", expected=etag)) == "already_ended"
    library.retract(ref, provenance="d", expected=etag).commit()  # retracting an ended relation is allowed
    _, etag = library.read(ref)
    assert reason(lambda: library.retract(ref, provenance="d", expected=etag)) == "already_retracted"
    assert reason(lambda: library.end(ref, until=Moment(12), provenance="d", expected=etag)) == "already_retracted"


def test_end_until_not_after_since_is_refused(library):
    ref = add(library, since=5).commit().relation
    _, etag = library.read(ref)
    for until in (5, 4):
        assert reason(lambda: library.end(ref, until=Moment(until), provenance="d", expected=etag)) == "invalid_until"


def test_ending_needs_no_duplicate_check(library):
    first = add(library, since=1, until=4).commit().relation
    add(library, since=6).commit()
    relation, etag = library.read(first)
    assert relation.ending is not None
    open_ref = add(library, subject=TOMAS, since=1).commit().relation
    add(library, subject=TOMAS, since=0, until=1).commit()
    _, etag = library.read(open_ref)
    library.end(open_ref, until=Moment(2), provenance="d", expected=etag).commit()  # only shrinks an interval


def test_undecodable_and_unsupported_cannot_be_rewritten(store, library):
    write(store.root, "campaigns/c/relations/r-8.json", relation_file("member_of", WREN, WREN, rid="r-8"))
    write(store.root, "campaigns/c/relations/r-9.json", relation_file())
    write(store.root, "campaigns/c/relations/r-7.json", b"{broken")
    before = snapshot(store.root)
    for rid, expected in (("r-8", "undecodable_relation"), ("r-9", "unsupported_relation")):
        ref = RelationRef(C, rid)
        etag = store.read(ref).etag
        assert reason(lambda: library.end(ref, until=Moment(9), provenance="d", expected=etag)) == expected
        assert reason(lambda: library.retract(ref, provenance="d", expected=etag)) == expected
    assert reason(lambda: library.read(RelationRef(C, "r-8"))) == "undecodable_relation"
    with pytest.raises(MalformedRecord):
        library.retract(RelationRef(C, "r-7"), provenance="d", expected="sha256:" + "0" * 64)
    assert snapshot(store.root) == before


def test_undecodable_file_does_not_block_add_and_is_reported(store, library):
    write(store.root, "campaigns/c/relations/r-8.json", relation_file("member_of", WREN, WREN, rid="r-8"))
    write(store.root, "campaigns/c/relations/r-7.json", b"{broken")
    library._new_id.counts["r"] = 8
    add(library).commit()
    graph = library.graph(C, requested_by=DIRECTOR)
    codes = {(p.code, p.relation_ids) for p in graph.index.problems}
    assert ("undecodable", ("r-8",)) in codes and ("undecodable", ("r-7",)) in codes
    assert graph.index.relation("r-7", requested_by=DIRECTOR).undecodable == "malformed"
    assert graph.index.relation("r-8", requested_by=DIRECTOR).undecodable == "self_relation"
    assert ("campaigns/c/relations/r-7.json", "malformed") in {(p.path, p.code) for p in graph.problems}


def test_unknown_predicate_is_reported_excluded_and_kept(store, library):
    write(store.root, "campaigns/c/relations/r-9.json", relation_file(**{"x-new": [1]}))
    data = (store.root / "campaigns/c/relations/r-9.json").read_bytes()
    index = library.graph(C, requested_by=DIRECTOR).index
    assert ("unknown_predicate", ("r-9",)) in {(p.code, p.relation_ids) for p in index.problems}
    assert index.traverse(WREN, as_of=9, requested_by=DIRECTOR).steps == ()
    assert [e.ref.relation_id for e in index.history(GUILD, requested_by=DIRECTOR)] == ["r-9"]
    assert (store.root / "campaigns/c/relations/r-9.json").read_bytes() == data


def test_dangling_endpoint_repair(tmp_path):
    root = tmp_path / "lib"
    with LibraryStore.create(root) as store:
        seed_campaign(store)
        library = relation_library(store)
        ref = add(library, "carries", TOMAS, LANTERN, since=1).commit().relation
        other = add(library, "located_in", LANTERN, HARBOR, since=1).commit().relation
    (root / "campaigns/c/instances/item/lantern.json").unlink()
    with LibraryStore.open(root) as store:  # reopened after the hand deletion
        library = relation_library(store)
        index = library.graph(C, requested_by=DIRECTOR).index
        assert ("missing_endpoint", (ref.relation_id,)) in {(p.code, p.relation_ids) for p in index.problems}
        assert index.traverse(TOMAS, as_of=5, requested_by=DIRECTOR).steps == ()
        assert [e.ref for e in index.history(LANTERN, requested_by=DIRECTOR)] == [ref, other]
        assert index.relation(ref.relation_id, requested_by=DIRECTOR).missing_endpoint
        _, etag = library.read(ref)
        library.end(ref, until=Moment(4), provenance="director: lost at sea", expected=etag).commit()
        _, etag = library.read(other)
        library.retract(other, provenance="director", expected=etag).commit()
        assert sorted(relation_endpoint_ids(store, C)) == ["harbor", "lantern", "tomas"]


def test_rewrite_keeps_owner_and_unknown_envelope_fields(store, library):
    ref = add(library).commit().relation
    target = store.root / "campaigns/c/relations/r-1.json"
    document = json.loads(target.read_bytes())
    document["x-envelope-note"] = {"kept": True}
    document["payload"]["x-payload-note"] = "kept"
    target.write_text(json.dumps(document))
    _, etag = library.read(ref)
    library.end(ref, until=Moment(9), provenance="d", expected=etag).commit()
    rewritten = json.loads(target.read_bytes())
    assert rewritten["x-envelope-note"] == {"kept": True} and rewritten["owner"] == document["owner"]
    assert rewritten["payload"]["x-payload-note"] == "kept"


# -- the graph view ---------------------------------------------------------------------------------

def test_graph_is_director_only(library):
    for who in ("player:p1", "", None):
        with pytest.raises(AuthorityError):
            library.graph(C, requested_by=who)


def test_graph_does_not_load_the_whole_library(store, library, monkeypatch):
    add(library).commit()
    monkeypatch.setattr(type(store), "load", lambda self: (_ for _ in ()).throw(AssertionError("whole load")))
    assert library.graph(C, requested_by=DIRECTOR).index.recorded("member_of", WREN, GUILD, as_of=3,
                                                                   requested_by=DIRECTOR)


def test_graph_reads_under_one_lock(store, library, monkeypatch):
    add(library).commit()
    depth = {"now": 0, "reads": []}
    real_lock = store.lock

    class Counting:
        def __enter__(self):
            depth["now"] += 1
            return real_lock.__enter__()

        def __exit__(self, *exc):
            depth["now"] -= 1
            return real_lock.__exit__(*exc)

    for name in ("campaign_relations", "campaign_instances"):
        real = getattr(store, name)
        monkeypatch.setattr(store, name, lambda *a, _real=real, _name=name: (
            depth["reads"].append((_name, depth["now"])), _real(*a))[1])
    monkeypatch.setattr(store, "lock", Counting())
    library.graph(C, requested_by=DIRECTOR)
    assert [d for _, d in depth["reads"]] == [1, 1]  # both reads inside the same outer acquisition


def test_relation_endpoint_ids(store, library):
    add(library).commit()
    write(store.root, "campaigns/c/relations/r-9.json", relation_file(subject=actor("gone"), obj=GUILD))
    write(store.root, "campaigns/c/relations/r-8.json", relation_file("member_of", actor("lost"), actor("lost"),
                                                                      rid="r-8"))
    assert relation_endpoint_ids(store, C) == frozenset({"wren", "ashen-guild", "gone"})  # not "lost"


def _loaded(module: str) -> set[str]:
    code = (f"import sys, json; import {module}; "
            f"print(json.dumps(sorted(m for m in {list(NETWORK_MODULES)!r} if m in sys.modules)))")
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True,
                         env={**os.environ, "PYTHONPATH": str(SRC)})
    return set(json.loads(out.stdout))


def test_no_provider_or_network_imports():
    assert _loaded("grimoire.graph") == set()  # the pure package reaches nothing
    assert _loaded("grimoire.graphstore") == _loaded("grimoire.store")  # the service adds nothing to the store's
    assert "grimoire.providers" not in _loaded("grimoire.graphstore")
