"""Ledger-versus-state checks of Sprint 021 (pure): receipts, orphans, the stack and wrap-up citations."""

from __future__ import annotations

from dataclasses import replace

import pytest

from grimoire.core import Moment
from grimoire.core.identity import AcceptedRef, ChatRef, RelationRef, WrapUpRef
from grimoire.core.knowledge import Event
from grimoire.effects import (
    EffectError, EndorsementRecord, Revocation, SceneWrapUp, Skipped, as_knowledge_event, check_citations,
    ledger_problems, orphans, verify_receipts, wrapup_id, wrapup_problems,
)
from grimoire.effects import revocation_fingerprint
from grimoire.graph import Ending, GraphIndex, Relation, Reopening, Retraction

from effects_fixtures import ANA, C, CAST, CHAT, KEY1, TAM, WREN, moment
from test_effects_model import event

ACC, REV = "effect:accepted/e-1", "effect:revoked/e-1"
END7 = Ending(Moment(7), ACC, "t")


def r1(**changes) -> Relation:
    base = dict(ref=RelationRef(C, "r-1"), predicate="carries", subject=WREN, object=KEY1, since=Moment(1),
                provenance="director", recorded_at="t", ending=END7)
    base.update(changes)
    return Relation(**base)


def r2(**changes) -> Relation:
    base = dict(ref=RelationRef(C, "r-2"), predicate="carries", subject=TAM, object=KEY1, since=Moment(7),
                provenance=ACC, recorded_at="t")
    base.update(changes)
    return Relation(**base)


def index(*relations) -> GraphIndex:
    return GraphIndex.build(C, relations, CAST)


def codes(problems) -> list[tuple]:
    return [(p.code, p.op, p.detail) for p in problems]


def revoked(item=None, skipped=()):
    item = item or event()
    return replace(item, revocation=Revocation("edited", "t2", "human:owner",
                                               (EndorsementRecord("c7", ANA, revocation_fingerprint(item.ref, item.fingerprint)),),
                                               tuple(skipped)))


def director_event(eid: str, ordinal: int, sequence: int, turn: str | None = None):
    """An NPC/world event at another turn: only the outcome key, so no contribution to match."""
    base = event()
    ops = tuple(replace(item, op=replace(item.op, agent=TAM, backed_by=None), domain="director")
                for item in base.operations)
    return replace(base, ref=AcceptedRef(C, eid), ordinal=ordinal, moment=moment(sequence, turn), operations=ops,
                   contributions=(), endorsements=(), controls=(),
                   receipts=tuple(replace(r, relation=RelationRef(C, f"{r.relation.relation_id}-{eid}"))
                                  for r in base.receipts))


def test_matching_receipts_verify():
    assert verify_receipts(event(), index(r1(), r2())) == ()
    assert verify_receipts(event(), index(r1(), r2(ending=Ending(Moment(9), "effect:accepted/e-2", "t")))) == ()


@pytest.mark.parametrize("relations, op, why", [
    ((r2(),), 0, "missing"),
    ((r1(ending=None), r2()), 0, "not ended by this event"),
    ((r1(ending=Ending(Moment(7), "director", "t")), r2()), 0, "not ended by this event"),
    ((r1(), r2(since=Moment(6))), 1, "not begun by this event"),
    ((r1(), r2(provenance="director")), 1, "not begun by this event"),
    ((r1(), r2(retraction=Retraction("d", "t"))), 1, "retracted"),
    ((r1(), r2(subject=WREN, since=Moment(7))), 1, "different fact"),
])
def test_mismatches_are_named(relations, op, why):
    assert ("receipt_mismatch", op, why) in codes(verify_receipts(event(), index(*relations)))


def test_revoked_receipts():
    undone = (r1(ending=None, reopenings=(Reopening(END7, REV, "t2"),)), r2(retraction=Retraction(REV, "t2")))
    assert verify_receipts(revoked(), index(*undone)) == ()
    assert codes(verify_receipts(revoked(), index(r1(), r2()))) == [
        ("receipt_mismatch", 0, "not reopened by this revocation"),
        ("receipt_mismatch", 1, "not retracted by this revocation")]
    later_retracted = r1(ending=None, reopenings=(Reopening(END7, REV, "t2"),),
                         retraction=Retraction("effect:revoked/e-0", "t3"))  # an earlier event revoked afterwards
    assert verify_receipts(revoked(), index(later_retracted, undone[1])) == ()
    partial = revoked(skipped=(Skipped(1, "changed"),))
    assert verify_receipts(partial, index(undone[0], r2())) == ()


def test_orphans_match_only_exact_tokens():
    assert orphans([event()], [r1(), r2()]) == ()
    stray = r2(ref=RelationRef(C, "r-9"), provenance="effect:accepted/e-404")
    freetext = r2(ref=RelationRef(C, "r-8"), provenance="accepted:gm-ruling")
    found = orphans([event()], [r1(), r2(), stray, freetext])
    assert [(p.code, p.event_id, p.relation_id) for p in found] == [("orphan_effect", "e-404", "r-9")]
    not_its_receipt = r2(ref=RelationRef(C, "r-7"))  # names e-1, which does not record r-7
    assert [p.relation_id for p in orphans([event()], [not_its_receipt])] == ["r-7"]
    revoked_token_on_active = r2(retraction=Retraction(REV, "t2"))
    assert [p.detail for p in orphans([event()], [r1(), revoked_token_on_active])] == ["revoked"]


def test_ledger_problems():
    a, b = event(), director_event("e-2", 2, 9)
    assert ledger_problems([a, b]) == ()
    assert [p.code for p in ledger_problems([a, replace(b, ordinal=1)])] == ["duplicate_ordinal"]
    backwards = director_event("e-2", 2, 5)
    assert [p.code for p in ledger_problems([a, backwards])] == ["inconsistent_ledger"]
    assert ledger_problems([revoked(a), backwards]) == ()  # a revoked event no longer orders the stack


def test_citations():
    a = event()
    check_citations(["e-1"], [a], CHAT, "s1", Moment(8))
    with pytest.raises(EffectError, match="uncited_event"):
        check_citations([], [a], CHAT, "s1", Moment(8))
    with pytest.raises(EffectError, match="unknown_event"):
        check_citations(["e-1", "e-9"], [a], CHAT, "s1", Moment(8))
    with pytest.raises(EffectError, match="revoked_event"):
        check_citations(["e-1"], [revoked(a)], CHAT, "s1", Moment(8))
    check_citations([], [a], CHAT, "s1", Moment(6))  # before the event
    check_citations([], [a], CHAT, "s2", Moment(8))  # another scene
    check_citations([], [a], ChatRef(C, "t-2"), "s1", Moment(8))  # another chat
    with pytest.raises(EffectError, match="unknown_event"):
        check_citations(["e-1"], [a], CHAT, "s2", Moment(8))


def test_wrapup_problems():
    ref = WrapUpRef(C, wrapup_id(CHAT, "s1"))
    wrap = SceneWrapUp(ref, CHAT, "s1", Moment(8), "x", ("e-1",), "director", "t")
    assert wrapup_problems([wrap], [event()]) == ()
    assert [p.code for p in wrapup_problems([wrap], [revoked()])] == ["stale_wrapup"]
    later = director_event("e-2", 2, 8)
    assert [(p.code, p.event_id) for p in wrapup_problems([wrap], [event(), later])] == [("incomplete_wrapup", "e-2")]
    assert [p.detail for p in wrapup_problems([wrap], [])] == ["missing"]


def test_as_knowledge_event_is_a_valid_012_event():
    found = as_knowledge_event(event())
    assert isinstance(found, Event)
    assert (found.event_id, found.moment, found.provenance) == ("e-1", moment(), ACC)
    assert found.participants == tuple(sorted({WREN, TAM, KEY1}, key=str))



def test_hand_edited_sequences_and_provenances_never_verify():
    """Each guard pinned by a hand edit: the active end's sequence, the revoked end's reopening sequence and
    the revoked begin's retraction provenance."""
    moved_end = r1(ending=Ending(Moment(5), ACC, "t"))
    assert ("receipt_mismatch", 0, "not ended by this event") in codes(verify_receipts(event(), index(moved_end, r2())))
    wrong_reopen = r1(ending=None, reopenings=(Reopening(Ending(Moment(5), ACC, "t"), REV, "t2"),))
    begun_undone = r2(retraction=Retraction(REV, "t2"))
    assert ("receipt_mismatch", 0, "not reopened by this revocation") in codes(
        verify_receipts(revoked(), index(wrong_reopen, begun_undone)))
    reopened = r1(ending=None, reopenings=(Reopening(END7, REV, "t2"),))
    foreign_retraction = r2(retraction=Retraction("editor", "t2"))
    assert ("receipt_mismatch", 1, "not retracted by this revocation") in codes(
        verify_receipts(revoked(), index(reopened, foreign_retraction)))
