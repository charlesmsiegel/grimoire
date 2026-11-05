"""Accepted effects that change counted holdings and charge ability costs (Sprint 021, next slice).

Acceptance scenarios 1, 2, 4 and 5 of the slice spec, persisted, and its failure cases (scenario 3, the crash
exploration, is in ``test_effectstore_recovery.py``). Orin is an uncontrolled NPC with a thruster pack and
3 fuel; Wren (``human:ana``) has 5 coins and Tam a knife. Every name is synthetic; nothing calls a model.
"""

from __future__ import annotations

import json

import pytest

from grimoire.core import DIRECTOR
from grimoire.core.identity import AcceptedRef, InstanceRef
from grimoire.core.model import ControlAssignment
from grimoire.effects import ActedLine, EffectHoldings, EffectProposal
from grimoire.effectstore import InvalidEffect
from grimoire.holdings import Line, Operation
from grimoire.holdingstore import HolderReplaced
from grimoire.store import LibraryStore, StaleWrite
from grimoire.testing.fileops import snapshot

from ability_fixtures import person
from effects_fixtures import (
    ANA, BOB, C, CHAT, CONTROLS, TAM, WREN, Handles, contribution, effects_library, give, moment, seed,
)
from graph_fixtures import hand_pair, seed_campaign
from holdings_fixtures import holdings_library
from parts_fixtures import KNIFE, ORIN, assessment, hop, pay, seed_holdings, trade, veil

ORIN_FILE = f"campaigns/{C}/holdings/character/orin.json"
TAM_FILE = f"campaigns/{C}/holdings/character/tam.json"
ORIN_INSTANCE = f"campaigns/{C}/instances/character/orin.json"
EVENT = AcceptedRef(C, "e-1")
TWO_PCS = (ControlAssignment(C, "wren", ANA), ControlAssignment(C, "tam", BOB))


@pytest.fixture
def world(tmp_path):
    handles = Handles()
    with LibraryStore.create(tmp_path / "lib") as store:
        seed(store, handles)
        seed_campaign(store, (ORIN, KNIFE))
        holdings = holdings_library(store)
        seed_holdings(store, holdings)
        yield store, handles, effects_library(store, handles), holdings


def counts(holdings, holder) -> dict[str, int]:
    return holdings.read(holder).counts()


def reason(call) -> str:
    with pytest.raises(InvalidEffect) as caught:
        call()
    return caught.value.reason


def accept_pay(library, handles, proposal=None, cid="c7"):
    proposal = proposal or pay()
    return library.accept(proposal, controls=CONTROLS, contributions=[contribution(cid, text="I pay the fare.")],
                          endorsements=[handles.endorse(proposal, cid)])


def revoke(library, ref=EVENT, *, endorsements=(), partial=False):
    _, etag = library.read(ref, requested_by=DIRECTOR)
    return library.revoke(ref, reason="rewind", expected=etag, endorsements=endorsements, partial=partial)


def continuity(library, as_of=9):
    return library.continuity(C, as_of=as_of, requested_by=DIRECTOR)


def codes(library) -> set[str]:
    return {problem.code for problem in continuity(library).problems}


def rewrite(store, relative: str, change) -> None:
    path = store.root / relative
    document = json.loads(path.read_bytes())
    change(document)
    path.write_bytes(json.dumps(document).encode())


# -- scenario 1: a hop charges its costs and burns fuel once ---------------------------------------

def test_s1_a_hop_charges_once_and_retries_are_no_ops(world):
    store, _, library, holdings = world
    change = library.accept(hop(), controls=CONTROLS)
    assert ORIN_FILE in change.preview()
    done = change.commit()
    assert done.ref == EVENT and counts(holdings, ORIN) == {"fuel": 2}
    event, _ = library.read(EVENT, requested_by=DIRECTOR)
    assert event.abilities[0].charges == (("thruster_pack", "cooldown"), ("thruster_pack", "fuel_burn"))
    assert event.abilities[0].classification == "known_capability" and event.holdings.seqs == {ORIN: 2}
    before = snapshot(store.root)
    again = library.accept(hop(), controls=CONTROLS)  # a retry of the very same effect
    assert again.duplicate_of == EVENT
    again.commit()
    assert snapshot(store.root) == before and counts(holdings, ORIN) == {"fuel": 2}
    assert reason(lambda: library.accept(hop(fuel=2), controls=CONTROLS)) == "turn_already_accepted"  # a reroll
    assert reason(lambda: library.accept(hop(sequence=8), controls=CONTROLS)) == "attempt_reused"  # a later turn
    assert continuity(library).problems == ()


def test_a_retry_after_reassessment_is_still_a_no_op(world):
    """A duplicate is told it is in force before the use is re-checked: an unpermitted re-assessment charges nothing."""
    _, _, library, holdings = world
    library.accept(hop(), controls=CONTROLS).commit()
    unarmed = assessment(definition=person("orin", "Orin"))
    assert not unarmed.permitted
    assert library.accept(hop(abilities=(unarmed,)), controls=CONTROLS).duplicate_of == EVENT
    assert counts(holdings, ORIN) == {"fuel": 2}


def test_an_unpermitted_use_is_refused(world):
    store, _, library, _ = world
    before = snapshot(store.root)
    unmet = hop(abilities=(assessment(conditions=()),))
    assert reason(lambda: library.accept(unmet, controls=CONTROLS)) == "ability_not_permitted"
    assert snapshot(store.root) == before


def test_holdings_refusals_pass_through(world):
    store, _, library, _ = world
    before = snapshot(store.root)
    assert reason(lambda: library.accept(hop(fuel=4), controls=CONTROLS)) == "insufficient"
    assert snapshot(store.root) == before


# -- scenario 2: two players' characters trade -------------------------------------------------------

def _trade(library, handles, *, endorse_tam=True):
    proposal = trade(tam_backed="c8")
    endorsements = [handles.endorse(proposal, "c7", ANA)] + ([handles.endorse(proposal, "c8", BOB)] if endorse_tam
                                                               else [])
    return library.accept(proposal, controls=TWO_PCS, endorsements=endorsements, contributions=[
        contribution("c7", text="I buy the knife."),
        contribution("c8", actor_id="tam", by=BOB, text="I hand Wren the knife.")])


def test_s2_two_players_each_endorse_their_own_line(world):
    _, handles, library, holdings = world
    assert reason(lambda: _trade(library, handles, endorse_tam=False)) == "not_endorsed"
    _trade(library, handles).commit()
    assert counts(holdings, WREN) == {"coin": 3, "knife": 1} and counts(holdings, TAM) == {"coin": 2}
    event, _ = library.read(EVENT, requested_by=DIRECTOR)
    assert event.holdings.domains == ("voluntary_action", "voluntary_action")
    assert {item.principal for item in event.endorsements} == {ANA, BOB}


def test_a_players_line_needs_their_contribution(world):
    _, handles, library, _ = world
    proposal = trade()  # Tam's line has no backing
    assert reason(lambda: library.accept(proposal, controls=TWO_PCS, contributions=[contribution()],
                                         endorsements=[handles.endorse(proposal)])) == "pc_authorship"


def test_a_players_ability_use_needs_their_contribution_and_endorsement(world):
    store, handles, library, _ = world
    proposal = EffectProposal(C, CHAT, moment(), "Wren bends the light around her.", (), "player", abilities=(veil(),))
    assert reason(lambda: library.accept(proposal, controls=CONTROLS)) == "pc_authorship"
    assert reason(lambda: library.accept(proposal, controls=CONTROLS,
                                         contributions=[contribution(text="I vanish.")])) == "not_endorsed"
    library.accept(proposal, controls=CONTROLS, contributions=[contribution(text="I vanish.")],
                   endorsements=[handles.endorse(proposal)]).commit()
    event, _ = library.read(EVENT, requested_by=DIRECTOR)
    assert event.abilities[0].domain == "voluntary_action" and event.abilities[0].charges == (
        ("light_bending", "eye_strain"),)
    assert event.holdings is None and event.attempts == {"c7"}
    assert library.accept(proposal, controls=CONTROLS, contributions=[contribution(text="I vanish.")],
                          endorsements=[handles.endorse(proposal)]).duplicate_of == EVENT
    revoke(library, endorsements=[handles.undo(EVENT, proposal)]).commit()
    stored = json.loads((store.root / f"campaigns/{C}/accepted/e-1.json").read_bytes())
    assert stored["schema"] == "grimoire.accepted-event/1"
    assert not {"holdings_skipped", "holdings_seqs"} & set(stored["payload"]["revocation"])
    assert reason(lambda: library.accept(proposal, controls=CONTROLS, contributions=[contribution(text="I vanish.")],
                                         endorsements=[handles.endorse(proposal)])) == "attempt_revoked"


def test_an_endorsement_binds_the_holdings_part(world):
    _, handles, library, _ = world
    stale = handles.endorse(pay(3))  # endorsed a different amount
    assert reason(lambda: library.accept(pay(), controls=CONTROLS, contributions=[contribution()],
                                         endorsements=[stale])) == "invalid_endorsement"


# -- scenario 4: revoking the hop gives the fuel back ------------------------------------------------

def test_s4_revoking_the_hop_restores_the_fuel(world):
    _, _, library, holdings = world
    library.accept(hop(), controls=CONTROLS).commit()
    revoke(library).commit()
    assert counts(holdings, ORIN) == {"fuel": 3}
    event, _ = library.read(EVENT, requested_by=DIRECTOR)
    assert event.revocation.holdings_skipped is None and event.revocation.holdings_seqs == {ORIN: 3}
    assert reason(lambda: library.accept(hop(), controls=CONTROLS)) == "attempt_revoked"  # a late retry
    assert continuity(library).problems == ()
    library.accept(hop(attempt_id="hop-2"), controls=CONTROLS).commit()  # a fresh attempt charges again
    assert counts(holdings, ORIN) == {"fuel": 2}


def test_a_revision_after_a_clean_revoke_keeps_its_attempt(world):
    _, _, library, holdings = world
    library.accept(hop(), controls=CONTROLS).commit()
    revoke(library).commit()
    library.accept(hop(fuel=2), controls=CONTROLS).commit()  # what was spent was given back: charge the revision
    assert counts(holdings, ORIN) == {"fuel": 1}


def test_an_inverse_already_logged_by_this_revocation_is_adopted(world):
    """An earlier publication landed the holder file but not the event: the retry adopts it, never undoes twice."""
    _, _, library, holdings = world
    library.accept(hop(), controls=CONTROLS).commit()
    change = revoke(library)
    (undo,) = change.holdings_changes
    undo.commit()
    assert counts(holdings, ORIN) == {"fuel": 3}
    # the event still reads active, but its fuel is back: a retry is never told the hop is in force
    assert reason(lambda: library.accept(hop(), controls=CONTROLS)) == "receipt_mismatch"
    assert {"receipt_mismatch", "orphan_effect"} <= codes(library)
    retry = revoke(library)
    assert retry.holdings_changes == ()
    retry.commit()
    event, _ = library.read(EVENT, requested_by=DIRECTOR)
    assert event.revocation.holdings_seqs == {ORIN: 3} and counts(holdings, ORIN) == {"fuel": 3}
    assert continuity(library).problems == ()


def test_an_undo_logged_by_some_holders_only_is_refused_not_adopted(world):
    """Half a revocation's holder files landed (Wren's, not Tam's): neither adopted nor undone again (030's)."""
    store, handles, library, holdings = world
    accept_pay(library, handles).commit()
    undo = [handles.undo(EVENT, pay())]
    wren = f"campaigns/{C}/holdings/character/wren.json"
    (store.root / wren).write_bytes(revoke(library, endorsements=undo).preview()[wren])
    assert counts(holdings, WREN) == {"coin": 5}  # Wren has the coins back; Tam still holds the fare
    assert reason(lambda: revoke(library, endorsements=undo)) == "receipt_mismatch"
    revoke(library, endorsements=undo, partial=True).commit()
    event, _ = library.read(EVENT, requested_by=DIRECTOR)
    assert event.revocation.holdings_skipped.startswith("partial_publication")
    assert "partial_revocation" in codes(library)


# -- scenario 5: coins spent since cannot be taken back ----------------------------------------------

def test_s5_spent_coins_block_the_undo_and_a_partial_revoke_records_it(world):
    _, handles, library, holdings = world
    accept_pay(library, handles).commit()
    holdings.apply(Operation("spend", (Line.consume(TAM, "coin", 2),))).commit()
    undo = [handles.undo(EVENT, pay())]
    assert reason(lambda: revoke(library, endorsements=undo)) == "has_dependents"
    revoke(library, endorsements=undo, partial=True).commit()
    event, _ = library.read(EVENT, requested_by=DIRECTOR)
    assert event.revocation.holdings_skipped.startswith("has_dependents") and event.revocation.holdings_seqs == {}
    assert counts(holdings, WREN) == {"coin": 3}
    assert codes(library) == {"partial_revocation"}
    assert reason(lambda: accept_pay(library, handles, pay(1))) == "attempt_revoked"  # a revision would spend again


# -- orphans and damage ---------------------------------------------------------------------------

def test_a_landed_holder_change_without_its_event_is_an_orphan(world):
    store, handles, library, holdings = world
    change = library.accept(hop(), controls=CONTROLS)
    (forward,) = change.holdings_changes
    forward.commit()  # as a publication that lost the event file would leave it
    assert counts(holdings, ORIN) == {"fuel": 2}
    assert codes(library) == {"orphan_effect"}
    assert reason(lambda: library.accept(hop(), controls=CONTROLS)) == "orphan_effect"
    assert reason(lambda: library.accept(hop(attempt_id="hop-2"), controls=CONTROLS)) == "orphan_effect"
    done = accept_pay(library, handles).commit()  # untouched holders are unaffected; e-1 stays the orphan's
    assert done.ref != EVENT and library.read(done.ref, requested_by=DIRECTOR)[0].holdings is not None
    assert not (store.root / f"campaigns/{C}/accepted/e-1.json").exists()


def test_a_damaged_holder_file_refuses_accept_duplicate_and_revoke(world):
    store, handles, library, _ = world
    library.accept(hop(), controls=CONTROLS).commit()
    (store.root / ORIN_FILE).write_bytes(b"{not json")
    assert reason(lambda: library.accept(hop(), controls=CONTROLS)) == "undecodable_holdings"  # a duplicate
    assert reason(lambda: revoke(library)) == "undecodable_holdings"
    assert reason(lambda: library.accept(hop(sequence=8, attempt_id="hop-2"),
                                         controls=CONTROLS)) == "undecodable_holdings"
    assert "undecodable_holdings" in codes(library)
    proposal = give(sequence=8)  # a relation-only effect does not depend on holder files
    library.accept(proposal, controls=CONTROLS, contributions=[contribution(turn="t8")],
                   endorsements=[handles.endorse(proposal)]).commit()


def test_a_damaged_holder_file_refuses_only_a_new_holdings_effect(world):
    store, handles, library, _ = world
    (store.root / TAM_FILE).write_bytes(b"{not json")
    assert reason(lambda: library.accept(hop(), controls=CONTROLS)) == "undecodable_holdings"  # it may name an id
    lift = hop(ops=(), abilities=None)
    ability_only = EffectProposal(C, CHAT, moment(), "Orin warms the pack.", (), "director", abilities=lift.abilities)
    library.accept(ability_only, controls=CONTROLS).commit()
    proposal = give(sequence=8)
    library.accept(proposal, controls=CONTROLS, contributions=[contribution(turn="t8")],
                   endorsements=[handles.endorse(proposal)]).commit()


def test_a_hand_edited_holder_log_is_a_receipt_mismatch(world):
    store, _, library, _ = world
    library.accept(hop(), controls=CONTROLS).commit()
    rewrite(store, ORIN_FILE, lambda d: d["payload"]["ops"][-1].update(op="hand-edit"))
    assert "receipt_mismatch" in codes(library)
    assert reason(lambda: library.accept(hop(), controls=CONTROLS)) == "receipt_mismatch"
    assert reason(lambda: revoke(library)) == "receipt_mismatch"


def test_an_evicted_forward_entry_cannot_be_undone(world):
    """An evicted entry cannot contradict the receipt (continuity is quiet) but cannot be told apart either."""
    store, _, library, holdings = world
    library.accept(hop(), controls=CONTROLS).commit()

    def evict(document):
        document["payload"]["evicted_through"] = document["payload"]["seq"]
        document["payload"]["ops"] = []
    rewrite(store, ORIN_FILE, evict)
    assert continuity(library).problems == ()
    assert reason(lambda: revoke(library)) == "receipt_mismatch"
    revoke(library, partial=True).commit()
    event, _ = library.read(EVENT, requested_by=DIRECTOR)
    assert "evicted" in event.revocation.holdings_skipped and counts(holdings, ORIN) == {"fuel": 2}
    assert codes(library) == {"partial_revocation"}


# -- holders that are no longer one individual ---------------------------------------------------------

def test_a_line_agent_or_ability_actor_needs_an_instance_file(world):
    """Spec step 8: the agent of a holdings line and the user of an ability are instances of the campaign."""
    _, _, library, holdings = world
    ghost = InstanceRef(C, "character", "ghost")
    lines = EffectHoldings((ActedLine(Line.consume(ORIN, "fuel", 1), ghost),))
    moved = EffectProposal(C, CHAT, moment(), "A stranger drains the tank.", (), "director", attempt_id="hop-1",
                           holdings=lines)
    assert reason(lambda: library.accept(moved, controls=CONTROLS)) == "missing_agent"
    assert reason(lambda: library.accept(hop(abilities=(assessment(ghost),)), controls=CONTROLS)) == "missing_agent"
    assert counts(holdings, ORIN) == {"fuel": 3}


def test_a_paired_holder_is_refused_at_accept_and_revoke(world):
    store, _, library, _ = world
    library.accept(hop(), controls=CONTROLS).commit()
    other = hand_pair(store, ORIN)
    assert reason(lambda: library.accept(hop(sequence=8, attempt_id="hop-2"),
                                         controls=CONTROLS)) == "instance_id_conflict"
    assert reason(lambda: revoke(library)) == "has_dependents"
    (store.root / f"campaigns/{C}/instances/{other.kind}/{other.instance_id}.json").unlink()


def test_a_replaced_holder_is_never_charged(world):
    store, _, library, holdings = world
    change = library.accept(hop(), controls=CONTROLS)
    rewrite(store, ORIN_INSTANCE, lambda d: d.update(payload={"note": "a different individual"}))
    before = snapshot(store.root)
    with pytest.raises(HolderReplaced):
        change.commit()
    assert snapshot(store.root) == before and counts(holdings, ORIN) == {"fuel": 3}


def test_a_holder_changed_since_the_plan_is_stale(world):
    store, _, library, holdings = world
    change = library.accept(hop(), controls=CONTROLS)
    holdings.apply(Operation("refuel", (Line.add(ORIN, "fuel", 1),))).commit()
    before = snapshot(store.root)
    with pytest.raises(StaleWrite):
        change.commit()
    assert snapshot(store.root) == before and counts(holdings, ORIN) == {"fuel": 4}
    library.accept(hop(), controls=CONTROLS).commit()  # re-planned, it lands once
    assert counts(holdings, ORIN) == {"fuel": 3}


# -- review follow-ups -----------------------------------------------------------------------------------

def test_a_skipped_undo_holds_every_attempt_of_the_event(world):
    """A revision reusing the ability attempt would burn fuel again for the same use that was never given back."""
    _, _, library, holdings = world
    handover = EffectProposal(C, CHAT, moment(), "Orin hops and hands Tam a fuel cell.", (), "director",
                              attempt_id="burn-1", holdings=EffectHoldings((Line.move(ORIN, TAM, "fuel", 1),)),
                              abilities=(assessment(attempt_id="hop-1"),))
    library.accept(handover, controls=CONTROLS).commit()
    holdings.apply(Operation("burn", (Line.consume(TAM, "fuel", 1),))).commit()  # Tam burned it since
    assert reason(lambda: revoke(library)) == "has_dependents"
    revoke(library, partial=True).commit()
    revision = hop(attempt_id="burn-2", abilities=(assessment(attempt_id="hop-1"),))
    assert reason(lambda: library.accept(revision, controls=CONTROLS)) == "attempt_revoked"
    assert reason(lambda: library.accept(hop(attempt_id="burn-1", abilities=(assessment(attempt_id="hop-2"),)),
                                         controls=CONTROLS)) == "attempt_revoked"


def test_an_undo_with_nothing_in_force_holds_nothing(world):
    """The holder was restored from before the hop: the revoke skips the undo, but nothing is held or reported."""
    store, _, library, holdings = world
    before = (store.root / ORIN_FILE).read_bytes()
    library.accept(hop(), controls=CONTROLS).commit()
    (store.root / ORIN_FILE).write_bytes(before)
    assert reason(lambda: revoke(library)) == "receipt_mismatch"
    revoke(library, partial=True).commit()
    event, _ = library.read(EVENT, requested_by=DIRECTOR)
    assert event.revocation.holdings_skipped.startswith("never applied") and not event.revocation.holdings_left
    assert continuity(library).problems == ()
    assert reason(lambda: library.accept(hop(), controls=CONTROLS)) == "attempt_revoked"  # the same effect, late
    library.accept(hop(fuel=2), controls=CONTROLS).commit()  # a revision keeps the attempt
    assert counts(holdings, ORIN) == {"fuel": 1}


def test_an_orphan_is_repaired_by_hand_and_then_a_fresh_attempt_lands(world):
    """The documented repair: undo (or keep) the change with a 023 operation and free the entry's cause."""
    store, _, library, holdings = world
    (forward,) = library.accept(hop(), controls=CONTROLS).holdings_changes
    forward.commit()
    holdings.apply(Operation("repair", (Line.add(ORIN, "fuel", 1),), cause="hand repair of a lost hop")).commit()

    def free(document):
        for entry in document["payload"]["ops"]:
            if entry.get("cause", "").startswith("effect:"):
                entry["cause"] = "hand repair: a hop that was never recorded"
    rewrite(store, ORIN_FILE, free)
    assert continuity(library).problems == ()
    assert reason(lambda: library.accept(hop(), controls=CONTROLS)) == "orphan_effect"  # its op id is still logged
    library.accept(hop(attempt_id="hop-2"), controls=CONTROLS).commit()
    assert counts(holdings, ORIN) == {"fuel": 2}


def test_a_deleted_holder_file_is_named_alike_everywhere(world):
    store, _, library, _ = world
    library.accept(hop(), controls=CONTROLS).commit()
    (store.root / ORIN_FILE).unlink()
    problems = continuity(library).problems
    assert [(p.code, p.detail) for p in problems] == [("receipt_mismatch", f"holdings: {ORIN}: holder restored")]
    assert reason(lambda: library.accept(hop(), controls=CONTROLS)) == "receipt_mismatch"
    assert reason(lambda: revoke(library)) == "receipt_mismatch"


def test_an_order_sensitive_effect_revokes_in_reverse(world):
    _, _, library, holdings = world
    lines = (Line.add(ORIN, "fuel", 2), Line.adjust(ORIN, "fuel", 5, 6))
    proposal = EffectProposal(C, CHAT, moment(), "Orin tops up the tank.", (), "director", attempt_id="fill-1",
                              holdings=EffectHoldings(lines))
    library.accept(proposal, controls=CONTROLS).commit()
    assert counts(holdings, ORIN) == {"fuel": 6}
    revoke(library).commit()
    assert counts(holdings, ORIN) == {"fuel": 3}


# -- brutal review round 1 follow-ups ------------------------------------------------------------------

def test_a_hole_in_the_log_is_not_taken_for_a_change_never_applied(world):
    """An entry deleted by hand leaves the fuel burned: the skip keeps the attempt and stays reported."""
    store, _, library, _ = world
    library.accept(hop(), controls=CONTROLS).commit()
    rewrite(store, ORIN_FILE, lambda d: d["payload"]["ops"].pop())
    revoke(library, partial=True).commit()
    event, _ = library.read(EVENT, requested_by=DIRECTOR)
    assert event.revocation.holdings_left and "not logged" in event.revocation.holdings_skipped
    assert codes(library) == {"partial_revocation"}
    assert reason(lambda: library.accept(hop(fuel=2), controls=CONTROLS)) == "attempt_revoked"


def test_an_unreadable_holder_file_is_reported_even_when_no_event_names_it(world):
    store, _, library, _ = world
    (forward,) = library.accept(hop(), controls=CONTROLS).holdings_changes
    forward.commit()  # an orphan, hidden once its file is damaged
    (store.root / ORIN_FILE).write_bytes(b"{not json")
    assert [(p.code, p.event_id) for p in continuity(library).problems] == [("undecodable_holdings", None)]


def test_a_revoke_that_landed_on_some_holders_only_keeps_the_attempt(world):
    """The event file and Wren's file landed, Tam's did not: Tam still has the coins, so the attempt is held."""
    store, handles, library, holdings = world
    accept_pay(library, handles).commit()
    published = revoke(library, endorsements=[handles.undo(EVENT, pay())]).preview()
    for path in (f"campaigns/{C}/accepted/e-1.json", f"campaigns/{C}/holdings/character/wren.json"):
        (store.root / path).write_bytes(published[path])
    assert counts(holdings, WREN) == {"coin": 5} and counts(holdings, TAM) == {"coin": 2, "knife": 1}
    assert "receipt_mismatch" in codes(library)
    assert reason(lambda: accept_pay(library, handles, pay(1))) == "attempt_revoked"


def test_a_landed_inverse_is_adopted_after_the_forward_entry_was_evicted(world):
    store, _, library, holdings = world
    library.accept(hop(), controls=CONTROLS).commit()
    (undo,) = revoke(library).holdings_changes
    undo.commit()

    def evict(document):  # as later operations would evict it
        document["payload"]["ops"] = [e for e in document["payload"]["ops"] if e["seq"] > 2]
        document["payload"]["evicted_through"] = 2
    rewrite(store, ORIN_FILE, evict)
    revoke(library).commit()
    event, _ = library.read(EVENT, requested_by=DIRECTOR)
    assert event.revocation.holdings_seqs == {ORIN: 3} and counts(holdings, ORIN) == {"fuel": 3}
    assert continuity(library).problems == ()


# -- brutal review round 2 follow-ups ------------------------------------------------------------------

def test_a_linked_holdings_folder_is_reported_not_hidden(world, tmp_path):
    store, _, library, _ = world
    (forward,) = library.accept(hop(), controls=CONTROLS).holdings_changes
    forward.commit()  # an orphan the link would hide
    folder = store.root / f"campaigns/{C}/holdings/character"
    moved = tmp_path / "elsewhere"
    folder.rename(moved)
    folder.symlink_to(moved, target_is_directory=True)
    assert "undecodable_holdings" in codes(library)
    assert reason(lambda: library.accept(hop(attempt_id="hop-2"), controls=CONTROLS)) == "undecodable_holdings"


def test_a_holder_restored_after_a_clean_revoke_frees_the_attempt(world):
    """Restoring before or after the revoke leaves the same files: the attempt is freed either way."""
    store, _, library, holdings = world
    before = (store.root / ORIN_FILE).read_bytes()
    library.accept(hop(), controls=CONTROLS).commit()
    revoke(library).commit()
    (store.root / ORIN_FILE).write_bytes(before)
    library.accept(hop(fuel=2), controls=CONTROLS).commit()
    assert counts(holdings, ORIN) == {"fuel": 1}
