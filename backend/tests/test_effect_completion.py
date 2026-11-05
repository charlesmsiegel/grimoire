"""Finishing or aborting a partly published acceptance exactly once (Sprint 030, first slice).

The capture scenario: Tam throws one ordinary ball at the wisp and they become allies, as one accepted effect.
Partial publications are built as the repo makes them: a prefix of the publish order (relations, event,
holder files) or a sync that delivered only some files. ``complete`` finishes shape A (event without all its
changes), ``adopt`` shape B (changes without their event); ``revoke(partial=True)`` aborts.
"""

from __future__ import annotations

import pytest

from grimoire.core import DIRECTOR
from grimoire.core.identity import AcceptedRef
from grimoire.effects import ActedLine, EffectHoldings, EffectProposal, Op, accepted_token, revoked_token
from grimoire.effectstore import EffectRecovery, InvalidEffect
from grimoire.graphstore import InvalidRelation
from grimoire.holdings import Line, Operation
from grimoire.store import LibraryStore, ProcessLocker, RecordEnvelope, RecordNotFound, StaleWrite

from capture_fixtures import BALL, WISP, allies, balls, planned, problems, publish, recovery, seed_capture, throw
from effects_fixtures import (
    C, CHAT, CONTROLS, INES, KEY1, TAM, WREN, Handles, contribution, effects_library, give, moment,
)
from graph_fixtures import relation_library
from holdings_fixtures import holdings_library
from store_helpers import owner_handle

EVENT = AcceptedRef(C, "e-1")
TAM_FILE = "campaigns/c/holdings/character/tam.json"


@pytest.fixture()
def world(tmp_path):
    store = LibraryStore.create(tmp_path / "lib", locker=ProcessLocker())
    handles = Handles()
    seed_capture(store, handles)
    yield store, handles
    store.close()


def _etag(store, handles, ref=EVENT):  # type: ignore[no-untyped-def]
    return effects_library(store, handles).read(ref, requested_by=DIRECTOR)[1]


def _refused(call, reason: str):  # type: ignore[no-untyped-def]
    with pytest.raises(InvalidEffect) as raised:
        call()
    assert raised.value.reason == reason, raised.value
    return raised.value


def _completed(store, handles) -> None:  # type: ignore[no-untyped-def]
    """One coherent outcome: event active, receipts verify, ball spent once, companion once, no problems."""
    assert balls(store) == 2
    assert len(allies(store)) == 1
    assert problems(store, handles) == []
    event, _ = effects_library(store, handles).read(EVENT, requested_by=DIRECTOR)
    assert event.active


# -- shape A: complete --------------------------------------------------------------------------

@pytest.mark.parametrize("landed", [dict(relations=True, event=True), dict(event=True, holders=True),
                                    dict(event=True)])
def test_complete_finishes_an_event_that_landed_without_some_changes(world, landed):
    store, handles = world
    publish(store, planned(store, handles), **landed)
    assert "receipt_mismatch" in problems(store, handles)
    _refused(lambda: effects_library(store, handles).accept(throw(), controls=CONTROLS), "receipt_mismatch")
    change = recovery(store, handles).complete(EVENT, expected=_etag(store, handles))
    written = set(change.preview())
    assert "campaigns/c/accepted/e-1.json" not in written  # the event file is never rewritten
    assert (TAM_FILE in written) == ("holders" not in landed)
    change.commit()
    _completed(store, handles)
    again = recovery(store, handles).complete(EVENT, expected=_etag(store, handles))  # the exactly-once retry
    assert again.duplicate_of == EVENT and again.preview() == {}
    again.commit()
    assert effects_library(store, handles).accept(throw(), controls=CONTROLS).duplicate_of == EVENT
    _completed(store, handles)


def test_a_completed_relation_is_rewritten_under_its_receipted_id_and_moment(world):
    store, handles = world
    change = planned(store, handles)
    publish(store, change, event=True, holders=True)
    recovery(store, handles).complete(EVENT, expected=_etag(store, handles)).commit()
    (relation,) = allies(store)
    assert relation.ref == change.relation_changes[0].relation
    assert relation.since == moment() and relation.provenance == accepted_token("e-1")


def test_complete_is_a_no_op_when_the_receipts_verify(world):
    store, handles = world
    planned(store, handles).commit()
    before = sorted(p.name for p in store.root.rglob("*.json"))
    change = recovery(store, handles).complete(EVENT, expected=_etag(store, handles))
    assert change.duplicate_of == EVENT and change.preview() == {}
    change.commit()
    assert sorted(p.name for p in store.root.rglob("*.json")) == before


def test_complete_refuses_a_revoked_event_and_a_stale_etag(world):
    store, handles = world
    publish(store, planned(store, handles), relations=True, event=True)
    with pytest.raises(StaleWrite):
        recovery(store, handles).complete(EVENT, expected="0" * 64)
    library = effects_library(store, handles)
    library.revoke(EVENT, reason="rewind", expected=_etag(store, handles), partial=True).commit()
    _refused(lambda: recovery(store, handles).complete(EVENT, expected=_etag(store, handles)), "already_revoked")
    with pytest.raises(RecordNotFound):  # the store's own error, as revoke
        recovery(store, handles).complete(AcceptedRef(C, "e-9"), expected="x")
    _refused(lambda: recovery(store, handles).complete("e-1", expected="x"), "invalid_field")  # type: ignore[arg-type]


def test_complete_works_on_the_latest_event_only(world):
    store, handles = world
    publish(store, planned(store, handles), relations=True, event=True)
    effects_library(store, handles).accept(Op_meet(), controls=CONTROLS).commit()
    _refused(lambda: recovery(store, handles).complete(EVENT, expected=_etag(store, handles)), "not_latest")


def Op_meet() -> EffectProposal:  # noqa: N802 - a later turn's effect, director-controlled
    return EffectProposal(C, CHAT, moment(9), "Tam meets Ines.", (Op("begin", "has_met", TAM, INES),), "director")


def test_a_begin_contradicted_by_an_overlapping_record_is_refused(world):
    store, handles = world
    publish(store, planned(store, handles), event=True, holders=True)
    relation_library(store).add(C, "allied_with", WISP, TAM, since=moment(3), provenance="editor",
                                relation_id="r-hand").commit()
    _refused(lambda: recovery(store, handles).complete(EVENT, expected=_etag(store, handles)), "precondition_failed")


def test_a_receipt_naming_a_different_fact_is_not_completable(world):
    store, handles = world
    change = planned(store, handles)
    publish(store, change, event=True, holders=True)
    receipted = change.relation_changes[0].relation
    hand = relation_library(store)
    hand._new_id = lambda prefix: receipted.relation_id  # type: ignore[attr-defined]
    hand.add(C, "knows", TAM, WISP, since=moment(), provenance="editor").commit()  # the id now holds another fact
    error = _refused(lambda: recovery(store, handles).complete(EVENT, expected=_etag(store, handles)),
                     "not_completable")
    assert error.records == ("0",)


def test_a_holder_that_moved_on_since_is_not_completable(world):
    store, handles = world
    publish(store, planned(store, handles), relations=True, event=True)
    holdings_library(store).apply(Operation("found", (Line.add(TAM, BALL, 1),))).commit()  # later direct 023 play
    _refused(lambda: recovery(store, handles).complete(EVENT, expected=_etag(store, handles)), "not_completable")


def test_an_undecodable_holder_or_relation_refuses_complete(world):
    store, handles = world
    change = planned(store, handles)
    publish(store, change, event=True)
    (store.root / TAM_FILE).write_bytes(b"{not json")
    _refused(lambda: recovery(store, handles).complete(EVENT, expected=_etag(store, handles)),
             "undecodable_holdings")
    (store.root / TAM_FILE).unlink()
    (store.root / "campaigns/c/relations/r-seed-2.json").write_bytes(b"{not json")
    _refused(lambda: recovery(store, handles).complete(EVENT, expected=_etag(store, handles)),
             "undecodable_relation")


def test_a_landed_holder_file_changed_between_plan_and_commit_is_stale(world):
    store, handles = world
    publish(store, planned(store, handles), event=True, holders=True)
    change = recovery(store, handles).complete(EVENT, expected=_etag(store, handles))
    holdings_library(store).apply(Operation("found", (Line.add(TAM, BALL, 1),))).commit()
    with pytest.raises(StaleWrite):
        change.commit()
    assert allies(store) == []


def test_a_landed_relation_changed_between_plan_and_commit_is_stale(world):
    store, handles = world
    publish(store, planned(store, handles), relations=True, event=True)
    change = recovery(store, handles).complete(EVENT, expected=_etag(store, handles))
    (relation,) = allies(store)
    graph = relation_library(store)
    graph.end(relation.ref, until=moment(8), provenance="editor", expected=graph.read(relation.ref)[1]).commit()
    with pytest.raises(StaleWrite):
        change.commit()
    assert balls(store) == 3


def test_complete_never_redoes_what_a_partly_published_revocation_undid(world):
    store, handles = world
    library = effects_library(store, handles)
    proposal = give(sequence=7)
    accepted = library.accept(proposal, controls=CONTROLS, contributions=[contribution()],
                              endorsements=[handles.endorse(proposal)])
    publish(store, accepted, event=True, relations=True)
    # A revocation published its relation changes but not its event file: the ending is reopened.
    revoke = library.revoke(EVENT, reason="rewind", expected=_etag(store, handles),
                            endorsements=[handles.undo(EVENT)])
    publish(store, revoke, relations=True)
    _refused(lambda: recovery(store, handles).complete(EVENT, expected=_etag(store, handles)), "not_completable")


def test_complete_never_re_ends_a_relation_this_event_ended_that_was_reopened(world):
    store, handles = world
    library = effects_library(store, handles)
    proposal = give(sequence=7)
    change = library.accept(proposal, controls=CONTROLS, contributions=[contribution()],
                            endorsements=[handles.endorse(proposal)])
    change.commit()
    ended = change.relation_changes[0].relation
    graph = relation_library(store)
    graph.reopen(ended, provenance="editor", expected=graph.read(ended)[1]).commit()  # a human undid the ending
    _refused(lambda: recovery(store, handles).complete(EVENT, expected=_etag(store, handles)), "not_completable")


def test_complete_pins_the_agents_instance_files(world):
    store, handles = world
    publish(store, planned(store, handles), relations=True, event=True)
    change = recovery(store, handles).complete(EVENT, expected=_etag(store, handles))
    txn = store.transaction()
    txn.put(RecordEnvelope(TAM, {"note": "a different Tam"}), store.read(TAM).etag)
    txn.commit(owner_handle())
    with pytest.raises(StaleWrite):
        change.commit()


# -- shape B: adopt -----------------------------------------------------------------------------

@pytest.mark.parametrize("landed", [dict(relations=True), dict(holders=True)])
def test_adopt_records_changes_that_landed_without_their_event(world, landed):
    store, handles = world
    publish(store, planned(store, handles), **landed)
    assert "orphan_effect" in problems(store, handles)
    change = recovery(store, handles).adopt(throw(), controls=CONTROLS)
    assert change.ref == EVENT
    assert ("campaigns/c/accepted/e-1.json" in change.preview()) and (TAM_FILE in change.preview()) != (
        "holders" in landed)
    change.commit()
    _completed(store, handles)
    again = recovery(store, handles).adopt(throw(), controls=CONTROLS)
    assert again.duplicate_of == EVENT and again.preview() == {}
    again.commit()
    _completed(store, handles)


def test_adopt_with_nothing_landed_is_refused(world):
    store, handles = world
    _refused(lambda: recovery(store, handles).adopt(throw(), controls=CONTROLS), "nothing_to_adopt")


def test_adopt_refuses_an_id_that_already_names_an_event_file(world):
    store, handles = world
    publish(store, planned(store, handles), relations=True)
    path = store.root / "campaigns/c/events/e-1.json"  # a 020 event file took the id (any bytes count)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{}")
    _refused(lambda: recovery(store, handles).adopt(throw(), controls=CONTROLS), "orphan_effect")


def test_adopt_refuses_an_orphan_with_another_history(world):
    store, handles = world
    publish(store, planned(store, handles), relations=True)
    relation_library(store).add(C, "knows", TAM, INES, since=moment(4), provenance=accepted_token("e-1")).commit()
    _refused(lambda: recovery(store, handles).adopt(throw(), controls=CONTROLS), "orphan_effect")


def test_adopt_refuses_an_orphan_with_a_revoked_token(world):
    store, handles = world
    publish(store, planned(store, handles), relations=True)
    relation_library(store).add(C, "knows", TAM, INES, since=moment(4), provenance=revoked_token("e-1")).commit()
    _refused(lambda: recovery(store, handles).adopt(throw(), controls=CONTROLS), "orphan_effect")


def test_adopt_refuses_an_entry_with_the_effects_op_id_under_a_free_text_cause(world):
    store, handles = world
    change = planned(store, handles)
    entry = change.holdings_changes[0].entries[TAM]
    holdings_library(store).apply(Operation(entry.op, entry.lines, entry.note, "repaired by hand")).commit()
    publish(store, change, relations=True)
    _refused(lambda: recovery(store, handles).adopt(throw(), controls=CONTROLS), "orphan_effect")


def test_adopt_needs_the_same_keys_as_accept(world):
    store, handles = world
    proposal = give(sequence=7)
    change = effects_library(store, handles).accept(proposal, controls=CONTROLS, contributions=[contribution()],
                                                    endorsements=[handles.endorse(proposal)])
    publish(store, change, relations=True)
    _refused(lambda: recovery(store, handles).adopt(proposal, controls=CONTROLS, contributions=[contribution()]),
             "not_endorsed")
    adopted = recovery(store, handles).adopt(proposal, controls=CONTROLS, contributions=[contribution()],
                                             endorsements=[handles.endorse(proposal)])
    adopted.commit()
    assert problems(store, handles) == []


def test_adopt_behind_a_later_active_event_is_a_stale_sequence(world):
    store, handles = world
    publish(store, planned(store, handles), relations=True)
    effects_library(store, handles).accept(Op_meet(), controls=CONTROLS).commit()
    _refused(lambda: recovery(store, handles).adopt(throw(), controls=CONTROLS), "stale_sequence")


def test_a_landed_relation_edited_between_plan_and_commit_is_stale(world):
    store, handles = world
    publish(store, planned(store, handles), relations=True)
    change = recovery(store, handles).adopt(throw(), controls=CONTROLS)
    (relation,) = allies(store)
    graph = relation_library(store)
    graph.end(relation.ref, until=moment(8), provenance="editor", expected=graph.read(relation.ref)[1]).commit()
    with pytest.raises(StaleWrite):
        change.commit()
    assert balls(store) == 3


def test_adopt_completes_a_two_holder_part_that_landed_on_one_holder(world):
    store, handles = world
    holdings_library(store).apply(Operation("coins", (Line.add(WREN, "coin", 5),))).commit()
    proposal = EffectProposal(C, CHAT, moment(7), "Tam pays the wisp's keeper.", (Op("begin", "allied_with", TAM, WISP,
                              TAM),), "director", attempt_id="throw-2",
                              holdings=EffectHoldings((ActedLine(Line.move(WREN, TAM, "coin", 2)),)))
    change = planned(store, handles, proposal)
    (wren_put,) = [put for put in change.holdings_changes[0].puts if put[0].address.holder == WREN]
    txn = store.transaction()
    txn.put(*wren_put)
    txn.commit(change.library.handle)  # a sync delivered only Wren's holder file
    adopted = recovery(store, handles).adopt(proposal, controls=CONTROLS)
    assert set(adopted.preview()) == {"campaigns/c/accepted/e-1.json", "campaigns/c/relations/r-1.json", TAM_FILE}
    adopted.commit()
    assert holdings_library(store).read(TAM).counts().get("coin") == 2
    assert holdings_library(store).read(WREN).counts().get("coin") == 3
    assert problems(store, handles) == []


# -- abort --------------------------------------------------------------------------------------

def test_shape_a_aborts_with_a_partial_revocation_and_a_late_retry_is_refused(world):
    store, handles = world
    publish(store, planned(store, handles), relations=True, event=True)  # companion, ball not spent
    effects_library(store, handles).revoke(EVENT, reason="crashed", expected=_etag(store, handles),
                                           partial=True).commit()
    assert balls(store) == 3 and allies(store) == []
    assert problems(store, handles) == []
    _refused(lambda: effects_library(store, handles).accept(throw(), controls=CONTROLS), "attempt_revoked")
    _refused(lambda: recovery(store, handles).adopt(throw(), controls=CONTROLS), "nothing_to_adopt")


def test_shape_b_aborts_by_adopting_then_revoking(world):
    store, handles = world
    publish(store, planned(store, handles), holders=True)  # the ball spent, no companion, no event
    recovery(store, handles).adopt(throw(), controls=CONTROLS).commit()
    effects_library(store, handles).revoke(EVENT, reason="crashed", expected=_etag(store, handles)).commit()
    assert balls(store) == 3 and allies(store) == []
    assert problems(store, handles) == []
    _refused(lambda: effects_library(store, handles).accept(throw(), controls=CONTROLS), "attempt_revoked")


# -- 017's chosen id -----------------------------------------------------------------------------

def test_017_add_under_a_chosen_id_is_create_only(world):
    store, handles = world
    graph = relation_library(store)
    change = graph.add(C, "knows", TAM, WISP, since=moment(), provenance="editor", relation_id="r-chosen")
    assert change.relation.relation_id == "r-chosen"
    change.commit()
    with pytest.raises(InvalidRelation) as raised:
        graph.add(C, "knows", TAM, INES, since=moment(), provenance="editor", relation_id="r-chosen")
    assert raised.value.reason == "relation_id_taken"
    with pytest.raises(ValueError):
        graph.add(C, "knows", TAM, INES, since=moment(), provenance="editor", relation_id="../x")


def test_recovery_takes_a_turn_effects_library():
    with pytest.raises(TypeError):
        EffectRecovery(object())  # type: ignore[arg-type]


__all__ = ["KEY1"]


def test_landed_changes_naming_two_events_are_ambiguous(world):
    store, handles = world
    first = planned(store, handles)
    other = effects_library(store, handles, new_id=lambda prefix: f"{prefix}-9").accept(throw(), controls=CONTROLS)
    assert other.ref == AcceptedRef(C, "e-9")
    publish(store, first, relations=True)
    publish(store, other, holders=True)  # the holder file of a second, separately allocated acceptance
    _refused(lambda: recovery(store, handles).adopt(throw(), controls=CONTROLS), "ambiguous_orphan")


# -- round-1 review: never double a human repair, never act on a history that arrives after the plan -------

def test_adopt_refuses_after_a_hand_refund_of_a_synced_spend(world):
    """Sync delivered only the holder file; a human refunded the ball. Adopting would let a later abort refund
    it a second time, so the holder having moved on since the effect's entry stops the adoption."""
    store, handles = world
    publish(store, planned(store, handles), holders=True)
    holdings_library(store).apply(Operation("refund-throw", (Line.add(TAM, BALL, 1),), "refund", "hand repair")
                                  ).commit()
    _refused(lambda: recovery(store, handles).adopt(throw(), controls=CONTROLS), "not_completable")
    assert balls(store) == 3 and allies(store) == []


def test_adopt_refuses_after_a_hand_charge_of_a_landed_relation(world):
    """The companion landed and a human charged the ball by hand: adopting would charge it again."""
    store, handles = world
    publish(store, planned(store, handles), relations=True)
    holdings_library(store).apply(Operation("charge-throw", (Line.consume(TAM, BALL, 1),), "throw", "hand repair")
                                  ).commit()
    _refused(lambda: recovery(store, handles).adopt(throw(), controls=CONTROLS), "not_completable")
    assert balls(store) == 2


def test_adopt_proceeds_when_the_holder_changed_only_before_the_relation_landed(world):
    store, handles = world
    publish(store, planned(store, handles), relations=True)
    recovery(store, handles).adopt(throw(), controls=CONTROLS).commit()  # the 3 balls were seeded earlier
    _completed(store, handles)


def test_a_token_arriving_between_adopt_and_its_commit_is_stale(world):
    store, handles = world
    publish(store, planned(store, handles), relations=True)
    change = recovery(store, handles).adopt(throw(), controls=CONTROLS)
    relation_library(store).add(C, "knows", TAM, INES, since=moment(4), provenance=revoked_token("e-1")).commit()
    with pytest.raises(StaleWrite):
        change.commit()
    _refused(lambda: recovery(store, handles).adopt(throw(), controls=CONTROLS), "orphan_effect")


def test_a_revocation_arriving_between_complete_and_its_commit_is_stale(world):
    store, handles = world
    publish(store, planned(store, handles), event=True, holders=True)
    change = recovery(store, handles).complete(EVENT, expected=_etag(store, handles))
    relation_library(store).add(C, "knows", TAM, INES, since=moment(4), provenance=revoked_token("e-1")).commit()
    with pytest.raises(StaleWrite):
        change.commit()
    assert allies(store) == []


def test_adopt_refuses_an_orphan_whose_own_relation_carries_its_revocation(world):
    """The event file is gone but its revocation already ended the companion: X has a revoked history."""
    store, handles = world
    planned(store, handles).commit()
    revocation = effects_library(store, handles).revoke(EVENT, reason="abort", expected=_etag(store, handles))
    publish(store, revocation, relations=True)
    (store.root / "campaigns/c/accepted/e-1.json").unlink()
    _refused(lambda: recovery(store, handles).adopt(throw(), controls=CONTROLS), "orphan_effect")


def test_complete_refuses_after_a_holder_only_partial_revocation(world):
    store, handles = world
    publish(store, planned(store, handles), event=True, holders=True)  # the companion never landed
    revocation = effects_library(store, handles).revoke(EVENT, reason="abort", expected=_etag(store, handles),
                                                       partial=True)
    publish(store, revocation, holders=True)  # the refund landed, the event is still active
    assert balls(store) == 3
    error = _refused(lambda: recovery(store, handles).complete(EVENT, expected=_etag(store, handles)),
                     "not_completable")
    assert error.records == ("revocation",)
    assert allies(store) == []


def _give_change(store, handles):  # type: ignore[no-untyped-def]
    proposal = give(sequence=7)
    return effects_library(store, handles).accept(proposal, controls=CONTROLS, contributions=[contribution()],
                                                  endorsements=[handles.endorse(proposal)])


def test_complete_finishes_an_end_that_never_landed(world):
    store, handles = world
    publish(store, _give_change(store, handles), event=True)
    assert problems(store, handles) != []
    recovery(store, handles).complete(EVENT, expected=_etag(store, handles)).commit()
    assert problems(store, handles) == []


def test_complete_finishes_the_end_when_only_the_begin_landed(world):
    store, handles = world
    change = _give_change(store, handles)
    txn = store.transaction()
    for put in [put for relation in change.relation_changes for put in relation.puts if put[1] is None]:
        txn.put(*put)  # the new holder relation, not the rewrite that ends the old one
    for put in change.puts:
        txn.put(*put)
    txn.commit(change.library.handle)
    recovery(store, handles).complete(EVENT, expected=_etag(store, handles)).commit()
    assert problems(store, handles) == []


def test_complete_refuses_a_begin_whose_endpoint_is_gone(world):
    store, handles = world
    publish(store, planned(store, handles), event=True, holders=True)
    [wisp] = [path for path in store.root.rglob("wisp.json") if "holdings" not in path.parts]
    wisp.unlink()
    _refused(lambda: recovery(store, handles).complete(EVENT, expected=_etag(store, handles)), "missing_endpoint")
    assert allies(store) == []


# -- round-2 review: complete never doubles a hand repair; adopt checks endpoints and odd times ----------

def test_complete_refuses_after_a_hand_refund_of_a_synced_spend(world):
    """Shape A from a sync (event and holder file, no companion), then a hand refund: completing would put the
    effect in force uncharged, and an abort would refund the ball again."""
    store, handles = world
    publish(store, planned(store, handles), event=True, holders=True)
    holdings_library(store).apply(Operation("refund-throw", (Line.add(TAM, BALL, 1),), "refund", "hand repair")
                                  ).commit()
    _refused(lambda: recovery(store, handles).complete(EVENT, expected=_etag(store, handles)), "not_completable")
    assert balls(store) == 3 and allies(store) == []


def _end_by_hand(store) -> None:  # type: ignore[no-untyped-def]
    (relation,) = allies(store)
    graph = relation_library(store)
    graph.end(relation.ref, until=moment(8), provenance="editor: not allies after all",
              expected=graph.read(relation.ref)[1]).commit()


def test_complete_refuses_after_the_companion_was_ended_by_hand(world):
    store, handles = world
    publish(store, planned(store, handles), relations=True, event=True)  # the ball was never spent
    _end_by_hand(store)
    _refused(lambda: recovery(store, handles).complete(EVENT, expected=_etag(store, handles)), "not_completable")
    assert balls(store) == 3


def test_adopt_refuses_after_the_orphan_companion_was_ended_by_hand(world):
    store, handles = world
    publish(store, planned(store, handles), relations=True)
    _end_by_hand(store)
    _refused(lambda: recovery(store, handles).adopt(throw(), controls=CONTROLS), "not_completable")
    assert balls(store) == 3


def test_adopt_refuses_a_landed_begin_whose_endpoint_is_gone(world):
    store, handles = world
    publish(store, planned(store, handles), relations=True)
    [wisp] = [path for path in store.root.rglob("wisp.json") if "holdings" not in path.parts]
    wisp.unlink()
    _refused(lambda: recovery(store, handles).adopt(throw(), controls=CONTROLS), "missing_endpoint")
    assert balls(store) == 3


def test_an_endpoint_removed_between_adopt_and_its_commit_is_stale(world):
    store, handles = world
    publish(store, planned(store, handles), relations=True)
    change = recovery(store, handles).adopt(throw(), controls=CONTROLS)
    [wisp] = [path for path in store.root.rglob("wisp.json") if "holdings" not in path.parts]
    wisp.unlink()
    with pytest.raises(StaleWrite):
        change.commit()
    assert balls(store) == 3


@pytest.mark.parametrize("stamp", ["synced from tablet", "2026-10-30T10:00:00"])
def test_adopt_refuses_a_landed_change_whose_time_cannot_be_read(world, stamp):
    """017 keeps any non-blank ``recorded_at``; one that is not an aware time cannot rule out a later repair."""
    import json
    store, handles = world
    change = planned(store, handles)
    publish(store, change, relations=True)
    [path] = list(store.root.rglob(f"{change.relation_changes[0].relation.relation_id}.json"))
    data = json.loads(path.read_text())
    (data.get("payload", data))["recorded_at"] = stamp
    path.write_text(json.dumps(data, sort_keys=True, indent=2) + "\n")
    assert allies(store)[0].recorded_at == stamp
    _refused(lambda: recovery(store, handles).adopt(throw(), controls=CONTROLS), "not_completable")


# -- round-3 review ------------------------------------------------------------------------------

def test_complete_refuses_a_verified_begin_whose_endpoint_is_gone(world):
    store, handles = world
    publish(store, planned(store, handles), relations=True, event=True)  # the ball was never spent
    [wisp] = [path for path in store.root.rglob("wisp.json") if "holdings" not in path.parts]
    wisp.unlink()
    _refused(lambda: recovery(store, handles).complete(EVENT, expected=_etag(store, handles)), "missing_endpoint")
    assert balls(store) == 3


def test_complete_refuses_an_event_whose_agent_is_gone(world):
    store, handles = world
    publish(store, _give_change(store, handles), event=True)
    [wren] = [path for path in store.root.rglob("wren.json") if "holdings" not in path.parts]
    wren.unlink()
    _refused(lambda: recovery(store, handles).complete(EVENT, expected=_etag(store, handles)), "missing_agent")


def test_a_retry_of_complete_after_a_later_event_is_still_a_no_op(world):
    store, handles = world
    publish(store, planned(store, handles), event=True, holders=True)
    recovery(store, handles).complete(EVENT, expected=_etag(store, handles)).commit()
    effects_library(store, handles).accept(Op_meet(), controls=CONTROLS).commit()
    retry = recovery(store, handles).complete(EVENT, expected=_etag(store, handles))
    assert retry.duplicate_of == EVENT and retry.preview() == {}
    retry.commit()
    _completed(store, handles)
