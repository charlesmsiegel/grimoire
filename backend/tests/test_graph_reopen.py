"""The additive 017 amendment for Sprint 021: reopening a relation's ending in place.

Reopening cancels an ending (it was revoked), keeps the relation's id, ``since``, provenance and
extras, and records the cancelled ending in ``reopenings``. A never-reopened file is byte-identical
to 017's encoding, and a pre-021 build (the 017 codec, kept verbatim as a fixture) reads a reopened
file as holding, with ``reopenings`` preserved as an unknown key.
"""

from __future__ import annotations

import pytest

from grimoire.core import DIRECTOR, Moment
from grimoire.core.identity import RelationRef
from grimoire.core.json_value import JsonObject
from grimoire.graph import Ending, GraphError, GraphIndex, Relation, Reopening, Retraction
from grimoire.graph.codec import RELATION_SCHEMA, RelationCodecError, relation_from_payload, relation_to_payload
from grimoire.graphstore import InvalidRelation
from grimoire.store import LibraryStore, StaleWrite

from fixtures import graph_codec_017 as old
from graph_fixtures import C, GUILD, INES, WREN, relation_library, seed_campaign

REF = RelationRef(C, "r-1")
END7 = Ending(Moment(7), "effect:accepted/e-1", "t1")


def relation(**changes) -> Relation:
    base = dict(ref=REF, predicate="member_of", subject=WREN, object=GUILD, since=Moment(1), provenance="director",
                recorded_at="t0")
    base.update(changes)
    return Relation(**base)


# -- model ----------------------------------------------------------------------------------------

def test_reopened_cancels_the_ending_and_keeps_everything_else():
    ended = relation(ending=END7, extras=JsonObject({"x-note": "kept"}))
    reopened = ended.reopened(Reopening(END7, "effect:revoked/e-1", "t2"))
    assert reopened.ending is None
    assert reopened.reopenings == (Reopening(END7, "effect:revoked/e-1", "t2"),)
    assert (reopened.ref, reopened.since, reopened.provenance, reopened.extras) == (
        ended.ref, ended.since, ended.provenance, ended.extras)
    assert all(reopened.holds_at(s) for s in (1, 7, 100)) and not ended.holds_at(7)


def test_reopen_refusals():
    with pytest.raises(GraphError, match="not_ended"):
        relation().reopened(Reopening(END7, "p", "t"))
    with pytest.raises(GraphError, match="not_ended"):  # it must cancel exactly the current ending
        relation(ending=END7).reopened(Reopening(Ending(Moment(8), "p", "t"), "p", "t"))
    with pytest.raises(GraphError, match="already_retracted"):
        relation(ending=END7, retraction=Retraction("p", "t")).reopened(Reopening(END7, "p", "t"))
    with pytest.raises(GraphError, match="invalid_until"):
        relation(since=Moment(7), reopenings=(Reopening(END7, "p", "t"),))
    with pytest.raises(GraphError):
        relation(reopenings=[Reopening(END7, "p", "t")])  # a list is not the frozen form


def test_reopen_then_end_again_then_reopen_again():
    first = relation(ending=END7).reopened(Reopening(END7, "a", "t2"))
    end9 = Ending(Moment(9), "b", "t3")
    second = first.ended(end9).reopened(Reopening(end9, "c", "t4"))
    assert [r.ending.until.sequence for r in second.reopenings] == [7, 9]
    assert relation_from_payload(REF, RELATION_SCHEMA, relation_to_payload(second)) == second


# -- codec ----------------------------------------------------------------------------------------

def test_a_never_reopened_relation_encodes_exactly_as_017():
    for item in (relation(), relation(ending=END7), relation(ending=END7, retraction=Retraction("p", "t"))):
        assert relation_to_payload(item) == old.relation_to_payload(item)
        assert "reopenings" not in relation_to_payload(item)


def test_a_pre_021_build_reads_a_reopened_file_as_holding():
    reopened = relation(ending=END7).reopened(Reopening(END7, "effect:revoked/e-1", "t2"))
    payload = relation_to_payload(reopened)
    seen = old.relation_from_payload(REF, RELATION_SCHEMA, payload)
    assert "reopenings" in seen.extras and seen.ending is None and seen.reopenings == ()
    assert [seen.holds_at(s) for s in range(12)] == [reopened.holds_at(s) for s in range(12)]
    assert old.relation_to_payload(seen) == payload  # its rewrite keeps the reopening verbatim


@pytest.mark.parametrize("value", [
    ["x"], [{}], [{"ending": {"until": {"sequence": 7}, "provenance": "p", "at": "t"}, "provenance": "p"}],
    [{"ending": {"until": {"sequence": 7}, "provenance": "p", "at": "t"}, "provenance": "p", "at": "t", "x": 1}],
    [{"ending": {"until": {"sequence": 1}, "provenance": "p", "at": "t"}, "provenance": "p", "at": "t"}],
])
def test_codec_refuses_malformed_reopenings(value):
    payload = {**relation_to_payload(relation()), "reopenings": value}
    with pytest.raises(RelationCodecError) as caught:
        relation_from_payload(REF, RELATION_SCHEMA, payload)
    assert caught.value.reason in {"invalid_field", "invalid_until"}


def test_reopenings_whose_clock_text_runs_backwards_still_round_trip():
    """Two writers with different clocks (a restart, NTP stepping back): list order, not clock text, is history."""
    late = Ending(Moment(7), "a", "t5")
    first = relation(ending=late).reopened(Reopening(late, "b", "2026-12-01T00:00:00Z"))
    early = Ending(Moment(9), "c", "t6")
    second = first.ended(early).reopened(Reopening(early, "d", "2026-01-01T00:00:00Z"))
    payload = relation_to_payload(second)
    assert relation_from_payload(REF, RELATION_SCHEMA, payload) == second


@pytest.mark.parametrize("value", [[], None])
def test_a_cleared_reopenings_key_means_never_reopened(value):
    payload = {**relation_to_payload(relation(ending=END7)), "reopenings": value}
    decoded = relation_from_payload(REF, RELATION_SCHEMA, payload)
    assert decoded == relation(ending=END7) and "reopenings" not in relation_to_payload(decoded)


@pytest.mark.parametrize("value", ["x", {}, {"note": ["kept", 1]}, 3, True])
def test_a_hand_authored_reopenings_key_that_is_not_a_list_stays_an_unknown_key(value):
    """017 preserved any unknown top-level key, so a file may already hold its own ``reopenings``. Only a
    list is 021's form; anything else is that older key, kept verbatim, and the relation still decodes."""
    payload = {**relation_to_payload(relation(ending=END7)), "reopenings": value}
    decoded = relation_from_payload(REF, RELATION_SCHEMA, payload)
    assert decoded.reopenings == () and decoded.ending == END7 and decoded.extras["reopenings"] == value
    assert relation_to_payload(decoded) == payload  # a rewrite keeps it verbatim
    with pytest.raises(GraphError) as caught:  # a reopen would have to overwrite it: refused, never lost
        decoded.reopened(Reopening(END7, "p", "t"))
    assert caught.value.reason == "reserved_key"


def test_a_hand_authored_reopenings_key_survives_an_end_rewrite():
    payload = {**relation_to_payload(relation()), "reopenings": "see the margin notes"}
    ended = relation_to_payload(relation_from_payload(REF, RELATION_SCHEMA, payload).ended(END7))
    assert ended["reopenings"] == "see the margin notes" and ended["ending"]["until"] == {"sequence": 7}


def test_index_flags_and_exposes_reopenings():
    reopened = relation(ending=END7).reopened(Reopening(END7, "x", "t2"))
    index = GraphIndex.build(C, [reopened], [WREN, GUILD])
    entry = index.relation("r-1", requested_by=DIRECTOR)
    assert entry.reopened and not entry.ended and entry.traversable
    assert index.history(WREN, requested_by=DIRECTOR)[0].relation.reopenings == reopened.reopenings
    assert index.to_json(requested_by=DIRECTOR)["relations"][0]["payload"]["reopenings"][0]["provenance"] == "x"
    assert index.recorded("member_of", WREN, GUILD, as_of=8, requested_by=DIRECTOR)


# -- service --------------------------------------------------------------------------------------

@pytest.fixture
def store(tmp_path):
    with LibraryStore.create(tmp_path / "lib") as opened:
        seed_campaign(opened)
        yield opened


@pytest.fixture
def library(store):
    return relation_library(store)


def _ended(library) -> tuple[RelationRef, str]:
    ref = library.add(C, "member_of", WREN, GUILD, since=Moment(1), provenance="director").commit().relation
    _, etag = library.read(ref)
    library.end(ref, until=Moment(7), provenance="director", expected=etag).commit()
    return ref, library.read(ref)[1]


def _reason(call) -> str:
    with pytest.raises(InvalidRelation) as caught:
        call()
    return caught.value.reason


def test_reopen_service_round_trip_keeps_owner_and_extras(store, library):
    ref, etag = _ended(library)
    before = store.read(ref).record
    library.reopen(ref, provenance="effect:revoked/e-1", expected=etag).commit()
    after, _ = library.read(ref)
    assert after.ending is None and after.reopenings[0].ending.until == Moment(7)
    assert store.read(ref).record.owner == before.owner and store.read(ref).record.extra == before.extra


def test_reopen_service_refusals(store, library):
    ref, etag = _ended(library)
    open_ref = library.add(C, "has_met", WREN, INES, since=Moment(1), provenance="d").commit().relation
    assert _reason(lambda: library.reopen(open_ref, provenance="p", expected=library.read(open_ref)[1])) == "not_ended"
    with pytest.raises(StaleWrite):
        library.reopen(ref, provenance="p", expected="sha256:" + "0" * 64)
    library.add(C, "member_of", WREN, GUILD, since=Moment(9), provenance="d").commit()  # the same fact, later
    assert _reason(lambda: library.reopen(ref, provenance="p", expected=etag)) == "duplicate_relation"


def test_reopen_of_a_retracted_relation_is_refused(library):
    ref, etag = _ended(library)
    library.retract(ref, provenance="d", expected=etag).commit()
    assert _reason(lambda: library.reopen(ref, provenance="p", expected=library.read(ref)[1])) == "already_retracted"


def test_reopen_overlap_appearing_after_preview_is_stale(store, library):
    ref, etag = _ended(library)
    change = library.reopen(ref, provenance="p", expected=etag)
    assert change.stale() == []
    library.add(C, "member_of", WREN, GUILD, since=Moment(9), provenance="d").commit()
    assert change.stale() == [(f"campaigns/{C}/relations/{ref.relation_id}.json", "duplicate recorded")]
    with pytest.raises(StaleWrite):
        change.commit()
    assert library.read(ref)[0].ending is not None


def test_stale_matches_the_commit_check_for_adds_and_the_store_cas_for_rewrites(store, library):
    change = library.add(C, "has_met", WREN, INES, since=Moment(2), provenance="d")
    assert change.stale() == library._stale_add(change.new, change.endpoint_etags) == []
    library.add(C, "has_met", INES, WREN, since=Moment(5), provenance="d").commit()  # reversed symmetric duplicate
    assert change.stale() == library._stale_add(change.new, change.endpoint_etags) != []
    ref, etag = _ended(library)
    rewrite = library.retract(ref, provenance="d", expected=etag)
    assert rewrite.stale() == []
    library.retract(ref, provenance="other", expected=etag).commit()
    path = f"campaigns/{C}/relations/{ref.relation_id}.json"
    assert rewrite.stale() == [(path, "changed")]
    with pytest.raises(StaleWrite) as caught:
        rewrite.commit()
    assert caught.value.stale == ((path, "changed"),)
    (store.root / path).unlink()
    assert rewrite.stale() == [(path, "absent")]
