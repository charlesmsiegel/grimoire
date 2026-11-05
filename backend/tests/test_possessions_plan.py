"""The pure unique-possession mutator and record codec (Sprint 034): rules, replay and round trips."""

from __future__ import annotations

import pytest

from grimoire.core.identity import InstanceRef
from grimoire.possessions import (
    MAX_LOG, POSSESSION_SCHEMA, AlreadyApplied, Creation, Origin, Possession, PossessionCodecError, PossessionRefused,
    State, Step,
    originate, party_ids, plan, possession_from_payload, possession_to_payload,
)

from possession_fixtures import AT, CHEST, HALL, HANDS, MARA, OTHER, TOMA, everyone, make, op, sword

LATER = "2026-11-05T11:00:00Z"


def run(operation, current, *, expected_rev=None, others=None, exists=everyone):
    return plan(operation, current, expected_rev=current.rev if expected_rev is None else expected_rev, at=LATER,
                exists=exists, others=others or {})


def refused(operation, current, **kwargs) -> str:
    with pytest.raises(PossessionRefused) as error:
        run(operation, current, **kwargs)
    return error.value.reason


def test_equipping_a_sword_another_character_owns_is_refused():
    maras = sword()  # Mara owns and holds it
    assert refused(op("e1", Step.equip(TOMA, "main-hand")), maras) == "not_owner"
    lent = sword(holder=TOMA)  # Toma holds it, but it is still Mara's
    assert refused(op("e1", Step.equip(TOMA, "main-hand")), lent) == "not_owner"
    assert refused(op("e1", Step.equip(MARA, "main-hand")), lent) == "not_holder"


def test_two_same_named_swords_keep_separate_slots_and_revisions():
    first, second = sword("sword-1"), sword("sword-2", rev=4)
    worn = run(op("e1", Step.equip(MARA, "main-hand")), first).value
    assert (worn.equipped, worn.rev, second.rev) == ("main-hand", 2, 4)
    assert refused(op("e2", Step.equip(MARA, "main-hand")), second, others={worn.item: worn}) == "slot_taken"
    off = run(op("e2", Step.equip(MARA, "off-hand")), second, others={worn.item: worn}).value
    assert (off.item, off.equipped, off.rev) == (second.item, "off-hand", 5)


@pytest.mark.parametrize("steps, reason", [
    ((Step.equip(MARA, "head"),), "slot_mismatch"),
    ((Step.unequip(),), "not_equipped"),
    ((Step.move(MARA),), "same_holder"),
    ((Step.own(MARA),), "same_owner"),
    ((Step.equip(MARA, "main-hand"), Step.move(TOMA)), "still_equipped"),
    ((Step.equip(MARA, "main-hand"), Step.own(TOMA)), "still_equipped"),
    ((Step.equip(MARA, "main-hand"), Step.equip(MARA, "off-hand")), "already_equipped"),
    ((Step.move(InstanceRef("c1", "item", "sword-1")),), "self_custody"),
    ((Step.own(InstanceRef("c1", "item", "sword-1")),), "self_owner"),
])
def test_invalid_steps_are_refused_and_nothing_changes(steps, reason):
    current = sword()
    assert refused(op("x1", *steps), current) == reason


def test_a_hand_over_moves_custody_and_ownership_in_one_revision():
    result = run(op("g1", Step.move(TOMA), Step.own(TOMA), note="a gift"), sword())
    assert (result.before.holder, result.before.owner) == (MARA, MARA)
    assert (result.after.holder, result.after.owner, result.value.rev) == (TOMA, TOMA, 2)
    entry = result.value.log[-1]
    assert (entry.op, entry.rev, entry.at, entry.note) == ("g1", 2, LATER, "a gift")
    worn = run(op("e1", Step.equip(TOMA, "main-hand")), result.value).value
    assert worn.equipped == "main-hand"
    assert refused(op("e2", Step.own(None)), worn) == "still_equipped"
    unowned = run(op("e2", Step.unequip(), Step.own(None)), worn).value
    assert (unowned.owner, unowned.equipped) == (None, None)


def test_a_steps_failure_refuses_the_whole_operation():
    current = sword()
    assert refused(op("m1", Step.move(TOMA), Step.equip(MARA, "main-hand")), current) == "not_holder"


def test_an_item_cannot_be_put_inside_what_it_holds():
    chest = Possession(CHEST, State(MARA, MARA), Origin("c-chest", AT, MARA, MARA, "Chest"))
    inside = run(op("m1", Step.move(CHEST)), sword()).value  # the sword goes into the chest
    assert inside.holder == CHEST
    assert refused(op("m2", Step.move(inside.item)), chest, others={inside.item: inside}) == "custody_cycle"
    deeper = run(op("m2", Step.move(HALL)), chest, others={inside.item: inside}).value
    assert deeper.holder == HALL
    # two levels down: the sword in a bag, the bag in the chest; the chest cannot go into the sword
    bag = run(op("m3", Step.move(CHEST)), sword("bag-1")).value
    deep = run(op("m4", Step.move(bag.item)), sword()).value
    others = {bag.item: bag, deep.item: deep}
    assert refused(op("m5", Step.move(deep.item)), deeper, others=others) == "custody_cycle"


def test_replay_and_revision_rules():
    current = sword()
    moved = run(op("m1", Step.move(TOMA)), current).value
    again = run(op("m1", Step.move(TOMA)), moved, expected_rev=1)  # a retry with the revision it first saw
    assert isinstance(again, AlreadyApplied) and again.entry.rev == 2
    assert refused(op("m1", Step.move(HALL)), moved) == "op_id_reused"
    assert refused(op("c-sword", Step.move(HALL)), moved) == "op_id_reused"  # the creation's id
    assert refused(op("m2", Step.move(HALL)), moved, expected_rev=1) == "stale_revision"
    assert refused(op("m2", Step.move(HALL)), moved, exists=lambda ref: ref != HALL) == "missing_party"


def test_shape_refusals():
    with pytest.raises(PossessionRefused) as error:
        op("m1", Step.move(TOMA), Step.move(OTHER))
    assert error.value.reason == "cross_campaign"
    assert refused(op("m1", Step.move(OTHER)), sword()) == "cross_campaign"
    with pytest.raises(PossessionRefused):
        Step("equip", by=MARA)  # no slot
    with pytest.raises(PossessionRefused):
        op("m1", *(Step.unequip(),) * 5)
    assert refused(op("m1", Step.move(TOMA)), sword(), expected_rev=0) == "invalid_field"


def test_the_log_is_bounded_and_never_drops_the_new_entry():
    current = sword()
    for n in range(MAX_LOG + 5):
        current = run(op(f"m{n}", Step.move(TOMA if n % 2 == 0 else MARA)), current).value
    assert len(current.log) == MAX_LOG
    assert current.log[-1].op == f"m{MAX_LOG + 4}"
    assert current.evicted_through == current.log[0].rev - 1 == 6
    assert refused(op("m0", Step.move(HALL)), current, expected_rev=2) == "stale_revision"  # evicted: never reapplied


def test_codec_round_trip_is_canonical_and_campaign_relative():
    current = run(op("g1", Step.move(TOMA), Step.own(TOMA), note="tab\tand\nlines", cause="effect:accepted/e-1"),
                  sword()).value
    current = run(op("e1", Step.equip(TOMA, "off-hand")), current).value
    payload = possession_to_payload(current)
    assert payload["schema"] == POSSESSION_SCHEMA
    assert payload["holder"] == "character/toma-1" and payload["equipped"] == "off-hand"
    assert possession_from_payload(current.item, payload) == current
    # the same record under another campaign's item (a copied campaign) names that campaign's parties
    fork = InstanceRef("c9", "item", "sword-1")
    forked = possession_from_payload(fork, payload)
    assert forked.holder == InstanceRef("c9", "character", "toma-1")
    assert (forked.rev, forked.origin.op, len(forked.log)) == (current.rev, current.origin.op, len(current.log))
    assert possession_to_payload(forked) == payload
    assert party_ids(payload) == {"mara-1", "toma-1"}


def test_unknown_top_level_fields_are_kept_and_unknowns_inside_refused():
    payload = possession_to_payload(sword())
    payload["x-condition"] = {"edge": "notched"}
    decoded = possession_from_payload(sword().item, payload)
    assert possession_to_payload(decoded)["x-condition"] == {"edge": "notched"}
    for change, reason in (
        (lambda p: p.update(schema="grimoire.possession/2"), "unsupported_schema"),
        (lambda p: p.update(instance_id="sword-9"), "identity_in_payload"),
        (lambda p: p["origin"].update(colour="red"), "unknown_field"),
        (lambda p: p.update(equipped="main-hand", owner="character/toma-1"), "invalid_field"),
        (lambda p: p.update(equipped="head"), "invalid_field"),
        (lambda p: p.update(holder="item/sword-1"), "invalid_field"),
        (lambda p: p.update(rev=True), "invalid_field"),
        (lambda p: p.pop("log"), "invalid_field"),
        (lambda p: p.update(log=[{"op": "m1", "rev": 2, "at": AT, "steps": [{"step": "fly"}],
                                  "before": {"holder": "character/mara-1"},
                                  "after": {"holder": "character/mara-1"}}]), "invalid_field"),
    ):
        broken = possession_to_payload(sword())
        change(broken)
        with pytest.raises(PossessionCodecError) as error:
            possession_from_payload(sword().item, broken)
        assert error.value.reason == reason


def test_originate_starts_at_revision_one_with_its_origin():
    creation = make(note="forged at the harbor")
    record = originate(creation, InstanceRef("c1", "item", "item-1"), at=AT, exists=everyone)
    assert (record.rev, record.holder, record.owner, record.fits) == (1, MARA, MARA, tuple(sorted(HANDS)))
    assert creation.same(record.origin) and not make(note="other").same(record.origin)
    with pytest.raises(PossessionRefused) as error:
        originate(Creation("make-1", MARA), InstanceRef("c1", "item", "item-1"), at=AT, exists=everyone)
    assert error.value.reason == "needs_name"
    with pytest.raises(PossessionRefused) as error:
        originate(creation, InstanceRef("c1", "item", "item-1"), at=AT, exists=lambda ref: ref != MARA)
    assert error.value.reason == "missing_party"
    with pytest.raises(PossessionRefused) as error:
        make(owner=OTHER)
    assert error.value.reason == "cross_campaign"
    for fits in ("head", None, b"head"):  # text is never split into one-letter slots
        with pytest.raises(PossessionRefused) as error:
            make(fits=fits)
        assert error.value.reason == "invalid_field"
    with pytest.raises(PossessionRefused):
        make(fits=tuple(f"slot-{n}" for n in range(17)))
    once = make(fits=iter(["main-hand"]))  # an iterator is read once, not used up by the check
    assert once.origin(AT).fits == ("main-hand",) and once.same(once.origin(AT))


def _logged(**entry):
    base = {"op": "m1", "rev": 2, "at": AT, "steps": [{"step": "move", "to": "character/toma-1"}],
            "before": {"holder": "character/mara-1", "owner": "character/mara-1"},
            "after": {"holder": "character/toma-1", "owner": "character/mara-1"}}
    base.update(entry)
    return base


@pytest.mark.parametrize("change", [
    lambda p: p.update(log=[_logged(steps=[{"step": "move", "to": "item/sword-1"}],
                                    after={"holder": "item/sword-1", "owner": "character/mara-1"})]),
    lambda p: p.update(log=[_logged(steps=[{"step": "equip", "by": "character/mara-1", "slot": "tail"}],
                                    after={"holder": "character/mara-1", "owner": "character/mara-1",
                                           "equipped": "tail"})]),
    lambda p: p.update(log=[_logged(), _logged(rev=3)]),  # one op id logged twice
    lambda p: p.update(log=[_logged(op="c-sword")]),  # the creation's id
    lambda p: p.update(log=[_logged(at="2026-02-30T00:00:00Z")]),
    lambda p: p.update(fits=[f"slot-{n}" for n in range(17)]),
])
def test_a_hand_edited_history_must_keep_the_records_invariants(change):
    payload = possession_to_payload(sword())
    payload["rev"] = 3
    change(payload)
    with pytest.raises(PossessionCodecError) as error:
        possession_from_payload(sword().item, payload)
    assert error.value.reason == "invalid_field"


def test_a_log_out_of_order_is_accepted_and_written_back_sorted():
    current = sword()
    for n in range(3):
        current = run(op(f"m{n}", Step.move(TOMA if n % 2 == 0 else MARA)), current).value
    payload = possession_to_payload(current)
    shuffled = dict(payload, log=list(reversed(payload["log"])))
    assert possession_to_payload(possession_from_payload(current.item, shuffled)) == payload


def test_a_logged_operation_in_another_campaign_is_refused():
    entry = run(op("m1", Step.move(TOMA)), sword()).value.log[0]
    with pytest.raises(PossessionRefused) as error:
        Possession(InstanceRef("c2", "item", "sword-1"), State(OTHER), Origin("c-sword", AT, OTHER), rev=2,
                   log=(entry,))
    assert error.value.reason == "cross_campaign"
