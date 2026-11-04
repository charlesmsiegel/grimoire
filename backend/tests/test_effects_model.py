"""Pure values of Sprint 021: operations, proposals, fingerprints, tokens and record invariants."""

from __future__ import annotations

import hashlib
from dataclasses import replace

import pytest

from grimoire.core import Moment
from grimoire.core.identity import AcceptedRef, ChatRef, InstanceRef, RelationRef, WrapUpRef
from grimoire.effects import (
    AcceptedEvent, AcceptedOperation, ContributionSnapshot, ControlSnapshot, EffectError, EffectProposal,
    EndorsementRecord, Op, Receipt, Revocation, SceneWrapUp, Skipped, accepted_token, fingerprint, parse_token,
    revoked_token, wrapup_id,
)

from effects_fixtures import ANA, C, CHAT, INES, KEY1, TAM, WREN, give, moment


def reason(call) -> str:
    with pytest.raises(EffectError) as caught:
        call()
    return caught.value.reason


# -- operations and proposals ---------------------------------------------------------------------

def test_op_rules():
    assert reason(lambda: Op("move", "carries", WREN, KEY1)) == "invalid_field"
    assert reason(lambda: Op("end", "loves", WREN, TAM)) == "unknown_predicate"
    assert reason(lambda: Op("end", "carries", WREN, WREN)) == "self_relation"
    assert reason(lambda: Op("end", "carries", "campaign:c/character/wren", KEY1)) == "invalid_field"
    assert reason(lambda: Op("end", "carries", WREN, KEY1, backed_by=" ")) == "invalid_field"
    assert Op("begin", "has_met", TAM, WREN).fact() == Op("begin", "has_met", WREN, TAM).fact()
    assert Op("begin", "carries", TAM, WREN).fact() != Op("begin", "carries", WREN, TAM).fact()


@pytest.mark.parametrize("change, expected", [
    (dict(moment=Moment(7)), "invalid_moment"),
    (dict(moment=Moment(7, "s1", "T7")), "invalid_moment"),
    (dict(summary=""), "invalid_field"),
    (dict(summary="x" * 4001), "invalid_field"),
    (dict(proposed_by="model"), "invalid_field"),
    (dict(operations=()), "no_operations"),
    (dict(operations=tuple(Op("begin", "has_met", WREN, InstanceRef(C, "character", f"n{i}")) for i in range(33))),
     "too_many_operations"),
    (dict(operations=(Op("end", "carries", WREN, KEY1), Op("begin", "carries", WREN, KEY1))),
     "conflicting_operations"),
    (dict(operations=(Op("begin", "has_met", WREN, TAM), Op("begin", "has_met", TAM, WREN))),
     "conflicting_operations"),
    (dict(operations=(Op("begin", "carries", InstanceRef("other", "character", "x"), KEY1),)), "campaign_mismatch"),
    (dict(operations=(Op("begin", "carries", TAM, KEY1, InstanceRef("other", "character", "x")),)),
     "campaign_mismatch"),
    (dict(chat=ChatRef("other", "t-1")), "campaign_mismatch"),
])
def test_proposal_rules(change, expected):
    base = give()
    fields = dict(campaign_id=C, chat=base.chat, moment=base.moment, summary=base.summary,
                  operations=base.operations, proposed_by=base.proposed_by)
    fields.update(change)
    assert reason(lambda: EffectProposal(**fields)) == expected


def test_fingerprint_ignores_wording_proposer_and_backing_but_not_turn_or_operations():
    base = give()
    assert give(summary="Another phrasing.", proposed_by="director").fingerprint == base.fingerprint
    assert give(backed="c8").fingerprint == base.fingerprint
    assert give(sequence=8).fingerprint != base.fingerprint
    assert give(turn="t7b").fingerprint != base.fingerprint
    assert give(scene="s2").fingerprint != base.fingerprint
    assert give(to=INES).fingerprint != base.fingerprint
    assert give(agent=None, backed=None).fingerprint != base.fingerprint
    assert give(chat=ChatRef(C, "t-2")).fingerprint != base.fingerprint


def test_fingerprint_golden_vector():
    """Stored endorsements pin fingerprints: this value must never change."""
    ops = (Op("end", "carries", WREN, KEY1, WREN), Op("begin", "has_met", TAM, WREN))
    assert fingerprint(CHAT, Moment(7, "s1", "t7"), ops) == (
        "sha256:" + hashlib.sha256(
            "chat=campaign:c#chat/t-1\nsequence=7\nscene=s1\nturn=t7\n"
            "end|carries|campaign:c/character/wren|campaign:c/item/key-1|campaign:c/character/wren\n"
            "begin|has_met|campaign:c/character/tam|campaign:c/character/wren|".encode()).hexdigest())


def test_tokens_are_exact():
    assert accepted_token("e-1") == "effect:accepted/e-1" and revoked_token("e-1") == "effect:revoked/e-1"
    assert parse_token("effect:accepted/e-1") == ("accepted", "e-1")
    assert parse_token("effect:revoked/e-1") == ("revoked", "e-1")
    for text in ("accepted:e-1", "effect:accepted/E-1", "effect:accepted/gm ruling", "director", None,
                 "effect:accepted/e-1 ", "effect:reverted/e-1"):
        assert parse_token(text) is None
    with pytest.raises(EffectError):
        accepted_token("Bad")


def test_wrapup_id_is_deterministic_and_distinguishes_scenes():
    assert wrapup_id(CHAT, "s1") == wrapup_id(CHAT, "s1")
    assert wrapup_id(CHAT, "s1") != wrapup_id(CHAT, "s2") != wrapup_id(ChatRef(C, "t-2"), "s1")
    assert wrapup_id(ChatRef(C, "a--b"), "c") != wrapup_id(ChatRef(C, "a"), "b--c")
    assert len(wrapup_id(CHAT, "s1")) == 26


# -- the accepted-event record --------------------------------------------------------------------

def event(**changes) -> AcceptedEvent:
    proposal = give()
    print_ = proposal.fingerprint
    base = dict(
        ref=AcceptedRef(C, "e-1"), chat=CHAT, moment=moment(), summary=proposal.summary, proposed_by="narration",
        operations=tuple(AcceptedOperation(op, "voluntary_action") for op in proposal.operations),
        contributions=(ContributionSnapshot("c7", "wren", ANA, "action", "s1", "t7", "I give Tam the key."),),
        endorsements=(EndorsementRecord("c7", ANA, print_),), controls=(ControlSnapshot("wren", ANA),),
        committed_by="human:owner", accepted_at="t", ordinal=1,
        receipts=(Receipt(0, RelationRef(C, "r-1")), Receipt(1, RelationRef(C, "r-2"))))
    base.update(changes)
    return AcceptedEvent(**base)


def test_a_valid_event():
    item = event()
    assert item.active and item.event_id == "e-1" and item.fingerprint == give().fingerprint


@pytest.mark.parametrize("change, expected", [
    (dict(receipts=(Receipt(0, RelationRef(C, "r-1")),)), "invalid_receipts"),
    (dict(receipts=(Receipt(1, RelationRef(C, "r-1")), Receipt(0, RelationRef(C, "r-2")))), "invalid_receipts"),
    (dict(receipts=(Receipt(0, RelationRef(C, "r-1")), Receipt(1, RelationRef(C, "r-1")))), "invalid_receipts"),
    (dict(receipts=(Receipt(0, RelationRef("x", "r-1")), Receipt(1, RelationRef(C, "r-2")))), "campaign_mismatch"),
    (dict(endorsements=()), "invalid_endorsement"),
    (dict(endorsements=(EndorsementRecord("c7", "human:bob", give().fingerprint),)), "invalid_endorsement"),
    (dict(endorsements=(EndorsementRecord("c7", ANA, "sha256:" + "0" * 64),)), "invalid_endorsement"),
    (dict(controls=(ControlSnapshot("wren", "human:bob"),)), "invalid_expression"),
    (dict(contributions=()), "pc_authorship"),
    (dict(contributions=(ContributionSnapshot("c7", "wren", ANA, "speech", "s1", "t7", "x"),)), "invalid_expression"),
    (dict(ordinal=0), "invalid_field"),
    (dict(chat=ChatRef("x", "t-1")), "campaign_mismatch"),
])
def test_event_invariants(change, expected):
    assert reason(lambda: event(**change)) == expected


def test_domain_must_match_backing():
    assert reason(lambda: AcceptedOperation(Op("end", "carries", WREN, KEY1, WREN, "c7"), "director")) == "invalid_domain"
    assert reason(lambda: AcceptedOperation(Op("end", "carries", WREN, KEY1, WREN), "voluntary_action")) == \
        "invalid_domain"


def test_revocation_invariants():
    item = event()
    good = Revocation("edited", "t2", "human:owner", (EndorsementRecord("c7", ANA, item.fingerprint),))
    assert not replace(item, revocation=good).active
    assert reason(lambda: replace(item, revocation=replace(good, endorsements=()))) == "invalid_endorsement"
    assert reason(lambda: replace(item, revocation=replace(good, skipped=(Skipped(2, "x"),)))) == "invalid_receipts"
    assert reason(lambda: Revocation("r", "t", "p", (), (Skipped(1, "x"), Skipped(0, "y")))) == "invalid_field"


def test_wrapup_invariants():
    ref = WrapUpRef(C, wrapup_id(CHAT, "s1"))
    good = SceneWrapUp(ref, CHAT, "s1", Moment(8), "Wren gave the key away.", ("e-1",), "director", "t")
    assert good.cites == ("e-1",)
    assert reason(lambda: replace(good, scene_id="s2")) == "wrapup_mismatch"
    assert reason(lambda: replace(good, cites=("e-2", "e-1"))) == "invalid_field"
    assert reason(lambda: replace(good, cites=["e-1"])) == "invalid_field"
    assert reason(lambda: replace(good, summary="x" * 20_001)) == "invalid_field"
    assert not hasattr(good, "operations") and not hasattr(good, "receipts")
