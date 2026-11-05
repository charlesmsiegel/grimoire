"""Holdings and ability parts of accepted effects (021 next slice): pure values, fingerprints, codecs, checks."""

from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import replace

import pytest

from grimoire.core import DIRECTOR, Moment
from grimoire.core.identity import AcceptedRef, InstanceRef
from grimoire.core.model import ControlAssignment
from grimoire.effects import (
    ACCEPTED_SCHEMA, ACCEPTED_SCHEMA_V1, AbilityCharge, AcceptedEvent, AcceptedHoldings, ActedLine,
    ContributionSnapshot, ControlSnapshot, EffectCodecError, EffectError, EffectHoldings, EffectProposal,
    EndorsementRecord, Revocation, Skipped,
    accepted_from_payload, accepted_schema, accepted_to_payload, as_knowledge_event, authorize, entry_failure,
    fingerprint, holdings_failures, holdings_op_id, holdings_orphans, holdings_partials, holdings_problems,
    inverse_lines, inverse_op_id, revocation_fingerprint,
)
from grimoire.holdings import Holdings, Line, LogEntry, Operation

from effects_fixtures import ANA, C, CHAT, CONTROLS, KEY1, TAM, WREN, contribution, give, moment
from parts_fixtures import ORIN, assessment, hop, meet, pay, trade, veil
from test_effects_model import event as legacy_event

REF = AcceptedRef(C, "e-1")
AT = "2026-11-01T10:00:00Z"


def reason(call) -> str:
    with pytest.raises((EffectError, EffectCodecError)) as caught:
        call()
    return caught.value.reason


# -- values ---------------------------------------------------------------------------------------

def test_holdings_lines_wrap_and_validate():
    part = EffectHoldings([Line.consume(ORIN, "fuel", 1)])
    assert part.lines == (ActedLine(Line.consume(ORIN, "fuel", 1)),) and part.holders == (ORIN,)
    assert reason(lambda: EffectHoldings(())) == "invalid_field"
    assert reason(lambda: EffectHoldings(("line",))) == "invalid_field"
    assert reason(lambda: EffectHoldings((Line.move(ORIN, InstanceRef("x", "character", "y"), "fuel", 1),))) \
        == "campaign_mismatch"
    assert reason(lambda: EffectHoldings(tuple(Line.add(ORIN, f"i{n}", 1) for n in range(33)))) == "too_large"
    assert reason(lambda: ActedLine(Line.add(ORIN, "fuel", 1), "campaign:c/character/orin")) == "invalid_field"
    assert reason(lambda: ActedLine(Line.add(ORIN, "fuel", 1), ORIN, " ")) == "invalid_field"


def test_inverse_lines_undo_in_reverse_order():
    lines = (Line.add(ORIN, "fuel", 2), Line.move(WREN, TAM, "coin", 3), Line.adjust(TAM, "knife", 1, 4),
             Line.consume(ORIN, "rope", 1))
    assert inverse_lines(lines) == (Line.add(ORIN, "rope", 1), Line.adjust(TAM, "knife", 4, 1),
                                    Line.move(TAM, WREN, "coin", 3), Line.consume(ORIN, "fuel", 2))
    assert inverse_lines(inverse_lines(lines)) == lines


def test_holdings_op_ids_are_derived_and_distinct_from_their_inverse():
    a = holdings_op_id(C, hop().fingerprint)
    assert a == holdings_op_id(C, hop().fingerprint) and a.startswith("h-") and len(a) == 34
    assert holdings_op_id(C, hop(fuel=2).fingerprint) != a
    assert inverse_op_id(a) == a + "-r"
    part = EffectHoldings((Line.consume(ORIN, "fuel", 1),))
    with pytest.raises(EffectError):
        AcceptedHoldings(part, ("director",), inverse_op_id(a), {ORIN: 1})


@pytest.mark.parametrize("change, expected", [
    (dict(attempt_id=None), "missing_attempt"),
    (dict(holdings=None, abilities=(), operations=(meet(),)), "invalid_field"),  # an attempt id names holdings
    (dict(attempt_id=" "), "invalid_field"),
    (dict(attempt_id="x" * 513), "invalid_field"),
    (dict(holdings=None, attempt_id=None, abilities=()), "no_operations"),
    (dict(abilities=(assessment(attempt_id="a"), assessment(attempt_id="a"))), "duplicate_attempt"),
    (dict(abilities=tuple(assessment(attempt_id=f"a{n}") for n in range(9))), "too_many_operations"),
    (dict(abilities=("assessment",)), "invalid_field"),
    (dict(holdings=EffectHoldings((ActedLine(Line.consume(ORIN, "fuel", 1), InstanceRef("x", "character", "y")),))),
     "campaign_mismatch"),
    (dict(abilities=(assessment(InstanceRef("x", "character", "orin")),)), "campaign_mismatch"),  # an ability actor
])
def test_proposal_attempt_rules(change, expected):
    base = hop()
    fields = dict(campaign_id=C, chat=base.chat, moment=base.moment, summary=base.summary, operations=(),
                  proposed_by="director", attempt_id=base.attempt_id, holdings=base.holdings,
                  abilities=base.abilities)
    fields.update(change)
    assert reason(lambda: EffectProposal(**fields)) == expected


def test_an_ability_use_alone_or_a_holdings_part_alone_is_an_effect():
    alone = EffectProposal(C, CHAT, moment(), "Orin hops.", (), "director", abilities=(assessment(),))
    assert alone.attempts == {"hop-1"} and alone.attempt_id is None
    assert pay().attempts == {"c7"}
    assert hop(abilities=(assessment(attempt_id="other"),)).attempts == {"hop-1", "other"}


def test_permitted_is_not_checked_when_proposing():
    refused = assessment(conditions=())  # the tank must hold fuel: unavailable
    assert not refused.permitted
    assert hop(abilities=(refused,)).abilities == (refused,)  # accept refuses it, after the duplicate check


# -- fingerprints ---------------------------------------------------------------------------------

def test_legacy_fingerprint_is_unchanged():
    assert fingerprint(CHAT, Moment(7, "s1", "t7"), give().operations) == give().fingerprint
    assert fingerprint(CHAT, Moment(7, "s1", "t7"), give().operations, attempt_id=None, holdings=None,
                       abilities=()) == give().fingerprint


def test_parts_fingerprint_golden_vector():
    """Stored endorsements pin fingerprints: this value must never change."""
    proposal = EffectProposal(C, CHAT, Moment(7, "s1", "t7"), "x", (meet(),), "director", attempt_id="hop-1",
                              holdings=EffectHoldings((ActedLine(Line.move(WREN, TAM, "coin", 2), WREN, "c7"),
                                                       Line.adjust(TAM, "knife", 1, 0))),
                              abilities=(assessment(attempt_id="hop-1"),))
    digest = hashlib.sha256(b"hop-1").hexdigest()
    text = ("chat=campaign:c#chat/t-1\nsequence=7\nscene=s1\nturn=t7\n"
            "begin|has_met|campaign:c/character/orin|campaign:c/character/tam|\n"
            f"attempt={digest}\n"
            "hline=move|campaign:c/character/wren|coin|2|||campaign:c/character/tam|campaign:c/character/wren\n"
            "hline=adjust|campaign:c/character/tam|knife||1|0||\n"
            f"ability={digest}|campaign:c/character/orin|thruster_pack|lift|flight,thrust|fuel")
    assert proposal.fingerprint == "sha256:" + hashlib.sha256(text.encode()).hexdigest()


def test_parts_fingerprint_covers_lines_in_order_and_ignores_wording():
    a, b = Line.add(ORIN, "fuel", 1), Line.add(ORIN, "rope", 1)
    one = EffectProposal(C, CHAT, moment(), "x", (), "director", attempt_id="z", holdings=EffectHoldings((a, b)))
    other = EffectProposal(C, CHAT, moment(), "x", (), "director", attempt_id="z", holdings=EffectHoldings((b, a)))
    assert one.fingerprint != other.fingerprint
    noted = EffectProposal(C, CHAT, moment(), "y", (), "narration", attempt_id="z",
                           holdings=EffectHoldings((a, b), "n"))
    assert noted.fingerprint == one.fingerprint
    assert pay(backed="c8").fingerprint == pay().fingerprint  # a backing is authority, not the effect
    assert pay(agent=None, backed=None).fingerprint != pay().fingerprint
    assert pay(attempt_id="c8").fingerprint != pay().fingerprint
    two = (assessment(attempt_id="a"), assessment(attempt_id="b"))
    assert hop(abilities=two).fingerprint == hop(abilities=two[::-1]).fingerprint  # uses are a set


# -- authority ------------------------------------------------------------------------------------

def test_holdings_lines_and_ability_uses_are_acts():
    found = authorize(pay(), CONTROLS, [contribution("c7")])
    assert found.holdings_domains == ("voluntary_action",) and found.endorsers == {"c7": ANA}
    assert reason(lambda: authorize(pay(), CONTROLS)) == "pc_authorship"
    assert reason(lambda: authorize(pay(agent=None), CONTROLS, [contribution("c7")])) == "unexpected_backing"
    orin = authorize(hop(), CONTROLS)
    assert orin.holdings_domains == ("director",) and orin.ability_domains == ("director",)
    wren = EffectProposal(C, CHAT, moment(), "Wren veils herself.", (), "player", abilities=(veil(),))
    assert reason(lambda: authorize(wren, CONTROLS)) == "pc_authorship"  # backed by the attempt's own contribution
    assert authorize(wren, CONTROLS, [contribution("c7")]).ability_domains == ("voluntary_action",)
    two = trade(tam_backed="c9")
    controls = (ControlAssignment(C, "wren", ANA), ControlAssignment(C, "tam", "human:bob"))
    found = authorize(two, controls, [contribution("c7"), contribution("c9", actor_id="tam", by="human:bob")])
    assert found.holdings_domains == ("voluntary_action",) * 2
    assert found.endorsers == {"c7": ANA, "c9": "human:bob"}


# -- the record and its codec ---------------------------------------------------------------------

def hop_event(**changes) -> AcceptedEvent:
    proposal = changes.pop("proposal", hop())
    op_id = holdings_op_id(C, proposal.fingerprint)
    base = dict(ref=REF, chat=CHAT, moment=proposal.moment, summary=proposal.summary, proposed_by="director",
                operations=(), contributions=(), endorsements=(), controls=(), committed_by="human:owner",
                accepted_at="t", ordinal=1, receipts=(), attempt_id=proposal.attempt_id,
                holdings=AcceptedHoldings(proposal.holdings, ("director",) * len(proposal.holdings.lines), op_id,
                                          {holder: 4 for holder in proposal.holdings.holders}),
                abilities=tuple(AbilityCharge.of(a, "director") for a in proposal.abilities))
    base.update(changes)
    return AcceptedEvent(**base)


def pay_event(**changes) -> AcceptedEvent:
    proposal = pay()
    op_id = holdings_op_id(C, proposal.fingerprint)
    base = dict(ref=REF, chat=CHAT, moment=proposal.moment, summary=proposal.summary, proposed_by="player",
                operations=(), contributions=(ContributionSnapshot("c7", "wren", ANA, "action", "s1", "t7", "I pay."),),
                endorsements=(EndorsementRecord("c7", ANA, proposal.fingerprint),),
                controls=(ControlSnapshot("wren", ANA),), committed_by="human:owner", accepted_at="t", ordinal=1,
                receipts=(), attempt_id="c7",
                holdings=AcceptedHoldings(proposal.holdings, ("voluntary_action",), op_id, {WREN: 2, TAM: 3}))
    base.update(changes)
    return AcceptedEvent(**base)


def test_charges_record_cost_ids():
    event = hop_event()
    assert event.abilities[0].charges == (("thruster_pack", "cooldown"), ("thruster_pack", "fuel_burn"))
    assert event.attempts == {"hop-1"} and event.has_parts and accepted_schema(event) == ACCEPTED_SCHEMA_V1
    assert reason(lambda: AbilityCharge.of(assessment(conditions=()), "director")) == "ability_not_permitted"


@pytest.mark.parametrize("make, expected", [
    (lambda: pay_event(contributions=(), endorsements=()), "pc_authorship"),
    (lambda: hop_event(holdings=AcceptedHoldings(hop().holdings, ("voluntary_action",), "h-1", {ORIN: 1})),
     "invalid_domain"),
    (lambda: hop_event(holdings=AcceptedHoldings(hop().holdings, ("director",), "h-1", {WREN: 1})),
     "invalid_receipts"),
    (lambda: hop_event(holdings=AcceptedHoldings(hop().holdings, ("director",), "h-1", {ORIN: 0})),
     "invalid_receipts"),
    (lambda: hop_event(attempt_id=None), "missing_attempt"),
    (lambda: hop_event(controls=(ControlSnapshot("orin", ANA),)), "invalid_domain"),
])
def test_event_invariants(make, expected):
    assert reason(make) == expected


def test_revocation_holdings_undo_shape():
    event = pay_event()
    endorsed = (EndorsementRecord("c7", ANA, revocation_fingerprint(REF, event.fingerprint)),)
    applied = Revocation("undo", "t2", "human:owner", endorsed, (), None, {WREN: 3, TAM: 4})
    assert replace(event, revocation=applied).revocation.holdings_seqs == {TAM: 4, WREN: 3}
    skipped = Revocation("undo", "t2", "human:owner", endorsed, (), "has_dependents: insufficient")
    assert not replace(event, revocation=skipped).active
    assert reason(lambda: replace(event, revocation=Revocation("undo", "t2", "h", endorsed))) == "invalid_receipts"
    assert reason(lambda: replace(event, revocation=Revocation("u", "t", "h", endorsed, (), None, {WREN: 3}))) \
        == "invalid_receipts"
    assert reason(lambda: Revocation("u", "t", "h", (), (), "why", {WREN: 3})) == "invalid_receipts"
    assert reason(lambda: replace(legacy_event(), revocation=Revocation(
        "u", "t", "h", (EndorsementRecord("c7", ANA, revocation_fingerprint(REF, legacy_event().fingerprint)),),
        (), "why"))) == "invalid_receipts"


def test_v1_round_trip_is_canonical():
    for item in (hop_event(), pay_event(), trade_event()):
        payload = accepted_to_payload(item)
        assert accepted_schema(item) == ACCEPTED_SCHEMA_V1
        assert accepted_from_payload(REF, ACCEPTED_SCHEMA_V1, payload) == item
        assert accepted_to_payload(accepted_from_payload(REF, ACCEPTED_SCHEMA_V1, payload)) == payload
    event = pay_event()
    endorsed = (EndorsementRecord("c7", ANA, revocation_fingerprint(REF, event.fingerprint)),)
    for revocation in (Revocation("undo", "t2", "h", endorsed, (), None, {WREN: 3, TAM: 4}),
                       Revocation("undo", "t2", "h", endorsed, (), "has_dependents: insufficient")):
        revoked = replace(event, revocation=revocation)
        assert accepted_from_payload(REF, ACCEPTED_SCHEMA_V1, accepted_to_payload(revoked)) == revoked


def trade_event() -> AcceptedEvent:
    proposal = trade()
    op_id = holdings_op_id(C, proposal.fingerprint)
    return AcceptedEvent(REF, CHAT, proposal.moment, proposal.summary, "player", (),
                         (ContributionSnapshot("c7", "wren", ANA, "action", "s1", "t7", "I buy it."),),
                         (EndorsementRecord("c7", ANA, proposal.fingerprint),),
                         (ControlSnapshot("tam", DIRECTOR), ControlSnapshot("wren", ANA)), "human:owner", "t", 1, (),
                         attempt_id="c7", holdings=AcceptedHoldings(proposal.holdings,
                                                                    ("voluntary_action", "director"), op_id,
                                                                    {WREN: 2, TAM: 3}))


#: sha256 of the sorted-key JSON of the legacy event and its revocation, as the pre-slice codec (v2 8f7146f) encoded
#: them: a /0 event, active or revoked, must encode byte for byte as before
LEGACY_GOLDEN = ("bd2789d010f04c9b1ad77a2d341f1967417aa5093ad48ca4e183ce13f4526eb7",
                 "ce429fe2b835865f9979aec961b7799549a3504476dbc071d6ffb0dd027429af")


def test_legacy_events_stay_v0_byte_identical():
    item = legacy_event()
    revoked = replace(item, revocation=Revocation(
        "rewind", "t2", "human:owner", (EndorsementRecord("c7", ANA, revocation_fingerprint(REF, item.fingerprint)),),
        (Skipped(1, "has_dependents: x"),)))
    digests = []
    for event in (item, revoked):
        assert accepted_schema(event) == ACCEPTED_SCHEMA and not event.has_parts
        payload = accepted_to_payload(event)
        assert not {"attempt_id", "holdings", "abilities"} & set(payload)
        assert not {"holdings_skipped", "holdings_seqs"} & set(payload.get("revocation") or {})
        assert reason(lambda: accepted_from_payload(REF, ACCEPTED_SCHEMA_V1, payload)) == "invalid_field"
        assert accepted_from_payload(REF, ACCEPTED_SCHEMA, payload) == event
        digests.append(hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest())
    assert tuple(digests) == LEGACY_GOLDEN


def test_ability_attempt_ids_are_stored_text():
    for bad in ("a\ud800", "x" * 513):
        assert reason(lambda: hop(abilities=(assessment(attempt_id=bad),))) == "invalid_field"
    assert reason(lambda: hop(abilities=(assessment(provenance="scene:s1/t7 \ud83d"),))) == "invalid_field"


@pytest.mark.parametrize("change", [dict(attempt=None), dict(other_costs=[]), dict(costs=None),
                                    dict(classification=["known_capability"]), dict(approval=None)])
def test_a_misshapen_assessment_is_refused(change):
    assert reason(lambda: hop(abilities=(replace(assessment(), **change),))) == "invalid_field"


def test_a_holdings_receipt_names_the_derived_operation():
    proposal = hop()
    for op_id in ("h-" + "0" * 32, "h" * 127):
        assert reason(lambda: hop_event(holdings=AcceptedHoldings(proposal.holdings, ("director",), op_id,
                                                                  {ORIN: 4}))) == "invalid_receipts"


def test_the_inverse_reverses_line_order():
    lines = (Line.add(ORIN, "fuel", 2), Line.adjust(ORIN, "fuel", 5, 6))
    assert inverse_lines(lines) == (Line.adjust(ORIN, "fuel", 6, 5), Line.consume(ORIN, "fuel", 2))


@pytest.mark.parametrize("mutate, expected", [
    (lambda p: p.pop("attempt_id"), "missing_attempt"),
    (lambda p: p["holdings"].update(extra=1), "unknown_field"),
    (lambda p: p["holdings"].update(note=None), "invalid_field"),
    (lambda p: p["holdings"]["lines"][0].update(domain="voluntary_action"), "invalid_domain"),
    (lambda p: p["holdings"]["lines"][0].pop("domain"), "missing_field"),
    (lambda p: p["holdings"]["lines"][0].update(amount=True), "invalid_field"),
    (lambda p: p["holdings"]["lines"][0].update(amount=3), "invalid_receipts"),  # the op id is no longer derived
    (lambda p: (p["holdings"]["lines"][0].update(amount=3), p["holdings"].update(
        op_id=holdings_op_id(C, pay(3).fingerprint))), "invalid_endorsement"),  # a new effect: unendorsed
    (lambda p: p["holdings"].update(seqs={}), "invalid_receipts"),
    (lambda p: p["holdings"].update(op_id="h-x-r"), "invalid_field"),
    (lambda p: p["abilities"][0].update(classification="new_ability"), "invalid_field"),  # approvable needs its ruling
    (lambda p: p["abilities"][0].update(charges=[["thruster_pack", "fuel_burn"], ["thruster_pack", "cooldown"]]),
     "invalid_field"),
    (lambda p: p["abilities"][0].update(needs=["thrust", "flight"]), "invalid_field"),
    (lambda p: p.update(abilities=[]), "invalid_field"),
    (lambda p: p["abilities"].append(copy.deepcopy(p["abilities"][0])), "duplicate_attempt"),
])
def test_v1_decoding_is_strict(mutate, expected):
    payload = copy.deepcopy(accepted_to_payload(pay_event() if expected == "invalid_endorsement" else hop_event()))
    mutate(payload)
    assert reason(lambda: accepted_from_payload(REF, ACCEPTED_SCHEMA_V1, payload)) == expected


def test_v0_payload_with_a_part_is_refused():
    payload = accepted_to_payload(legacy_event())
    payload["holdings"] = accepted_to_payload(pay_event())["holdings"]
    assert reason(lambda: accepted_from_payload(REF, ACCEPTED_SCHEMA, payload)) == "invalid_field"
    v1 = accepted_to_payload(hop_event())
    assert reason(lambda: accepted_from_payload(REF, ACCEPTED_SCHEMA, v1)) == "invalid_field"


# -- receipts against holder logs -----------------------------------------------------------------

def logged(holder: InstanceRef, op: Operation, seq: int, *, evicted: int = 0, total: int | None = None,
           extra: tuple[LogEntry, ...] = ()) -> Holdings:
    entry = LogEntry(op.op_id, seq, AT, op.lines, note=op.note, cause=op.cause)
    log = tuple(sorted((entry, *extra), key=lambda e: e.seq))
    return Holdings(holder, total or seq, evicted, {}, log)


def test_entry_failure_lenient_and_strict():
    event = hop_event()
    op = event.holdings.holdings.operation(event.holdings.op_id, "effect:accepted/e-1")
    assert entry_failure(logged(ORIN, op, 4), 4, op) is None
    other = Operation(op.op_id, op.lines, "different note", op.cause)
    assert entry_failure(logged(ORIN, other, 4), 4, op) == "different operation"
    assert entry_failure(Holdings(ORIN, 2), 4, op) == "holder restored"
    assert entry_failure(Holdings(ORIN, 9, 5), 4, op) is None  # evicted: it cannot contradict
    assert entry_failure(Holdings(ORIN, 9, 5), 4, op, strict=True) == "evicted"
    assert entry_failure(Holdings(ORIN, 9, 3), 4, op) == "not logged"


def test_holdings_failures_and_problems():
    event = hop_event()
    op = event.holdings.holdings.operation(event.holdings.op_id, "effect:accepted/e-1")
    assert holdings_failures(event, {ORIN: logged(ORIN, op, 4)}) == ()
    assert holdings_failures(event, {ORIN: "undecodable (x)"}) == ((ORIN, "undecodable (x)", True),)
    assert holdings_failures(event, {}) == ((ORIN, "not read", True),)
    found = holdings_problems([event], {ORIN: Holdings(ORIN, 9, 3)})
    assert [(p.code, p.op, p.detail) for p in found] == [
        ("receipt_mismatch", None, "holdings: campaign:c/character/orin: not logged")]
    assert holdings_problems([event], {ORIN: "undecodable (x)"})[0].code == "undecodable_holdings"


def test_a_revoked_event_is_held_to_its_inverse():
    event = hop_event()
    revoked = replace(event, revocation=Revocation("u", "t", "h", (), (), None, {ORIN: 5}))
    inverse = event.holdings.holdings.inverse(event.holdings.op_id, "effect:revoked/e-1")
    forward = event.holdings.holdings.operation(event.holdings.op_id, "effect:accepted/e-1")
    held = logged(ORIN, inverse, 5, extra=(LogEntry(forward.op_id, 4, AT, forward.lines, cause=forward.cause),))
    assert holdings_failures(revoked, {ORIN: held}) == ()
    assert holdings_failures(revoked, {ORIN: logged(ORIN, forward, 4)}) == ((ORIN, "holder restored", False),)
    skipped = replace(event, revocation=Revocation("u", "t", "h", (), (), "has_dependents: insufficient"))
    assert holdings_failures(skipped, {}) == ()
    assert [(p.code, p.detail) for p in holdings_partials([skipped, event])] == [
        ("partial_revocation", "holdings: has_dependents: insufficient")]


def test_holdings_orphans_name_unrecorded_021_causes():
    event = hop_event()
    forward = event.holdings.holdings.operation(event.holdings.op_id, "effect:accepted/e-1")
    stray = Operation("h-stray", (Line.add(ORIN, "fuel", 1),), None, "effect:accepted/e-9")
    free = Operation("seed", (Line.add(ORIN, "rope", 1),), None, "a gift from the director")
    held = logged(ORIN, forward, 4, total=6, extra=(LogEntry("h-stray", 5, AT, stray.lines, cause=stray.cause),
                                                    LogEntry("seed", 6, AT, free.lines, cause=free.cause)))
    found = holdings_orphans([event], {ORIN: held})
    assert [(p.code, p.event_id, p.detail) for p in found] == [
        ("orphan_effect", "e-9", "holdings campaign:c/character/orin")]
    assert [p.event_id for p in holdings_orphans([], {ORIN: held})] == ["e-1", "e-9"]
    inverse = event.holdings.holdings.inverse(event.holdings.op_id, "effect:revoked/e-1")
    early = logged(ORIN, inverse, 5)  # an undo logged while the event is still active
    assert [p.event_id for p in holdings_orphans([event], {ORIN: early})] == ["e-1"]


def test_bridge_participants_include_holders_agents_and_users():
    assert set(as_knowledge_event(hop_event()).participants) == {ORIN}
    assert set(as_knowledge_event(trade_event()).participants) == {WREN, TAM}
    # relation-only events: unchanged from the pre-slice bridge (subjects, objects and agents of the operations)
    assert as_knowledge_event(legacy_event()).participants == tuple(sorted((KEY1, TAM, WREN), key=str))
