"""Durable player sovereignty and ledger codecs (010b S6, S7)."""

from __future__ import annotations

import pytest

from grimoire.core.authority import (
    AuthorityError, Correction, NarrationClaim, NarrationContext, PlayerContribution, Revocation,
    apply_corrections, auto_complete, delegate, review_narration,
)
from grimoire.core.model import ControlAssignment
from grimoire.ledgers import AuthorityUnavailable, ControlChange, LedgerBroken, LedgerInvalid, SovereigntyLedger
from grimoire.ledgers.codec import (
    decode_approval, decode_decision, decode_grant, decode_sovereignty, encode_approval, encode_decision,
    encode_grant, encode_sovereignty,
)
from grimoire.security import Approval, AuditRecord, Authorizer, Grant, Permission, Reason, ResourcePath
from grimoire.store import LibraryStore, LinkRefused, ProcessLocker, etag
from grimoire.store.jsonfmt import parse_strict, render
from grimoire.store.ledger import render_batches

from ledger_helpers import OWNER, open_authority
from store_helpers import make_dir_link

P1, P2 = "human:p1", "human:p2"


@pytest.fixture
def library(tmp_path):
    locker = ProcessLocker()
    store = LibraryStore.create(tmp_path / "lib", locker=locker)
    authority = open_authority(store)
    yield store, authority, locker
    authority.close()
    store.close()


def _reopen(store, authority, locker):
    authority.close()
    store.close()
    store = LibraryStore.open(store.root, locker=locker)
    authority = open_authority(store)
    return store, authority


# -- codecs ------------------------------------------------------------------------------------

VALUES = [
    ControlChange("c1", "wren", P1), ControlChange("c1", "wren", None),
    delegate("characterization", campaign_id="c1", delegation_id="d1", actor_id="wren", granted_by=P1,
             scope="scene", scene_id="s1"),
    Revocation("c1", "d1", P1), Correction("c1", "claim-7", P1, "she would never say that"),
]


@pytest.mark.parametrize("value", VALUES, ids=lambda v: type(v).__name__)
def test_sovereignty_values_round_trip_deterministically(value):
    kind, fields = encode_sovereignty(value)
    data = render(fields)
    decoded = decode_sovereignty(kind, parse_strict(data))
    assert decoded == value and render(encode_sovereignty(decoded)[1]) == data


def test_security_values_round_trip_deterministically():
    grant = Grant("g1", "app:x", frozenset({Permission.APPLY, Permission.INSPECT}), ResourcePath.parse("worlds"),
                  OWNER)
    approval = Approval("a1", "app:x", Permission.APPLY, ResourcePath.parse("worlds/w/x.json"),
                        "sha256:" + "0" * 64, OWNER)
    record = AuditRecord(3, "app:x", Permission.APPLY, "worlds/w/x.json", False, True, Reason.APPROVED, "g1", "a1")
    for encode, decode, value in [(encode_grant, decode_grant, grant), (encode_decision, decode_decision, record)]:
        data = render(encode(value))
        assert decode(parse_strict(data)) == value and render(encode(decode(parse_strict(data)))) == data
    data = render(encode_approval(approval, "g1"))
    assert decode_approval(parse_strict(data)) == (approval, "g1")


@pytest.mark.parametrize("mutate", [
    lambda d: d.update(extra=1), lambda d: d.pop("scope"), lambda d: d.update(permissions="apply"),
    lambda d: d.update(scope="authority"), lambda d: d.update(granted_by="app:x"),
])
def test_grant_decoding_is_exact(mutate):
    fields = encode_grant(Grant("g1", "app:x", frozenset({Permission.APPLY}), ResourcePath.parse("worlds"), OWNER))
    mutate(fields)
    with pytest.raises(LedgerInvalid):
        decode_grant(parse_strict(render(fields)))


def test_decision_decoding_rejects_string_bools():
    fields = encode_decision(AuditRecord(1, "app:x", None, None, False, False, Reason.INTERNAL_ERROR, None, None))
    fields["trusted"] = "false"
    with pytest.raises(LedgerInvalid):
        decode_decision(parse_strict(render(fields)))


# -- S7 round trip ------------------------------------------------------------------------------

def test_sovereignty_survives_a_restart_and_reviews_the_same(library):
    store, authority, locker = library
    ledger = SovereigntyLedger(authority)
    owner, p1, p2 = (authority.handle_for(who, trusted=True) for who in (OWNER, P1, P2))
    ledger.record("c1", ControlChange("c1", "wren", P1), owner)
    ledger.record("c1", ControlChange("c1", "wren", P2), owner)
    ledger.record("c1", ControlChange("c1", "wren", P1), owner)
    ledger.record("c1", ControlChange("c1", "gull", P2), owner)
    ledger.record("c1", ControlChange("c1", "gull", None), owner)
    contribution = PlayerContribution("c1", "k1", "s1", "t1", "wren", P1, "action", "I head to the stables")
    ledger.record("c1", delegate("incidental_dialogue", campaign_id="c1", delegation_id="d1", actor_id="wren",
                                 granted_by=P1, scope="scene", scene_id="s1"), p1)
    ledger.record("c1", auto_complete(contribution, "d2"), p1)
    ledger.record("c1", Revocation("c1", "d1", P1), p1)
    ledger.record("c1", Correction("c1", "claim-1", P1, "not my words"), p1)
    before = ledger.load("c1")
    store, authority = _reopen(store, authority, locker)
    after = SovereigntyLedger(authority).load("c1")
    assert after == before
    assert after.controls == (ControlAssignment("c1", "wren", P1),)
    assert [(e.actor_id, e.controller_id) for e in after.control_history] == [
        ("wren", P1), ("wren", P2), ("wren", P1), ("gull", P2), ("gull", None)]
    context = NarrationContext("c1", "s1", "t1")
    claims = [NarrationClaim("c1", "claim-1", "voluntary_speech", "Wren says hello", subject_id="wren"),
              NarrationClaim("c1", "claim-2", "voluntary_action", "Wren heads out", subject_id="wren",
                             expresses="k1")]

    def review(state):
        reviewed = review_narration(context, claims, state.controls, [contribution], state.delegations,
                                    state.revocations)
        return reviewed, apply_corrections(reviewed, state.corrections, state.controls)
    assert review(after) == review(before)
    assert [r.claim.claim_id for r in review(after)[0].rejected] == ["claim-1"]  # d1 stays revoked
    authority.close()
    store.close()


# -- S6/S7 refusals -------------------------------------------------------------------------------

def test_only_the_named_player_or_an_owner_can_record(library):
    store, authority, locker = library
    ledger = SovereigntyLedger(authority)
    owner, p1, p2 = (authority.handle_for(who, trusted=True) for who in (OWNER, P1, P2))
    ledger.record("c1", delegate("incidental_dialogue", campaign_id="c1", delegation_id="d1", actor_id="wren",
                                 granted_by=P1, scope="campaign"), p1)
    refusals = [
        (delegate("incidental_dialogue", campaign_id="c1", delegation_id="d9", actor_id="wren", granted_by=P1,
                  scope="campaign"), p2),                                  # forged grantor
        (Revocation("c1", "d1", P1), p2),                                  # forged revoker
        (Revocation("c1", "d7", P2), p2),                                  # unknown delegation
        (delegate("incidental_dialogue", campaign_id="c1", delegation_id="d1", actor_id="wren", granted_by=P1,
                  scope="campaign"), p1),                                  # duplicate id
        (Correction("c1", "claim-1", P1), p2),                             # forged corrector
        (ControlChange("c1", "wren", P2), p2),                             # non-owner control
        (ControlChange("c2", "wren", P2), owner),                          # other campaign
        (Correction("c1", "claim-1", P1, "x" * 5000), p1),                 # oversized note
    ]
    for value, handle in refusals:
        with pytest.raises(AuthorityError):
            ledger.record("c1", value, handle)
    for handle in (authority.handle_for(P1), authority.handle_for("app:importer", trusted=True),
                   Authorizer([OWNER]).handle_for(P1, trusted=True)):
        with pytest.raises(AuthorityError):
            ledger.record("c1", Correction("c1", "claim-1", P1), handle)
    assert len(ledger.load("c1").delegations) == 1


def test_a_hand_forged_entry_in_a_valid_tail_is_refused(library):
    store, authority, locker = library
    ledger = SovereigntyLedger(authority)
    ledger.record("c1", ControlChange("c1", "wren", P1), authority.handle_for(OWNER, trusted=True))
    head = store.read_ledger("campaigns/c1").head
    forged = {"schema": 1, "kind": "control", "recorded_at": "x", "recorded_by": P2,
              "value": {"campaign_id": "c1", "actor_id": "wren", "controller_id": P2}}
    parts, data = render_batches("campaigns/c1", head, [forged])[0]
    (store.root / "/".join(parts)).write_bytes(data)
    store, authority = _reopen(store, authority, locker)
    with pytest.raises(LedgerInvalid, match="owner"):
        SovereigntyLedger(authority).load("c1")
    authority.close()
    store.close()
    assert head == (1, etag((store.root / "authority/campaigns/c1/0000000001.json").read_bytes()))


def test_ledgers_over_one_authority_share_state(library):
    store, authority, locker = library
    p1 = authority.handle_for(P1, trusted=True)
    first, second = SovereigntyLedger(authority), SovereigntyLedger(authority)
    value = delegate("incidental_dialogue", campaign_id="c1", delegation_id="d1", actor_id="wren", granted_by=P1,
                     scope="campaign")
    first.load("c1")
    second.load("c1")
    first.record("c1", value, p1)
    with pytest.raises(AuthorityError, match="already exists"):
        second.record("c1", value, p1)
    assert len(store.read_ledger("campaigns/c1").entries) == 1 and not authority.poisoned


def test_an_external_batch_poisons_instead_of_writing_over_it(library):
    store, authority, locker = library
    ledger = SovereigntyLedger(authority)
    owner = authority.handle_for(OWNER, trusted=True)
    ledger.record("c1", ControlChange("c1", "wren", P1), owner)
    head = store.read_ledger("campaigns/c1").head
    outside = {"schema": 1, "kind": "control", "recorded_at": "x", "recorded_by": OWNER,
               "value": {"campaign_id": "c1", "actor_id": "wren", "controller_id": P2}}
    parts, data = render_batches("campaigns/c1", head, [outside])[0]
    (store.root / "/".join(parts)).write_bytes(data)
    with pytest.raises(AuthorityUnavailable):
        ledger.record("c1", ControlChange("c1", "gull", P1), owner)
    assert authority.poisoned and (store.root / "/".join(parts)).read_bytes() == data
    assert len(store.read_ledger("campaigns/c1").batches) == 2


@pytest.mark.parametrize("value, campaign", [
    (ControlChange("c1", "wren", "human:" + "x" * 600), "c1"),
    (Correction("c1", "claim-1", OWNER, "x" * 5000), "c1"),
    (ControlChange("c1", "wren", P1), "Not A Segment"),
    (ControlChange("c1", "wren", P1), "../c1"),
])
def test_oversized_or_misaddressed_values_are_refused_without_poison(library, value, campaign):
    store, authority, locker = library
    with pytest.raises(AuthorityError):
        SovereigntyLedger(authority).record(campaign, value, authority.handle_for(OWNER, trusted=True))
    assert not authority.poisoned and not (store.root / "authority" / "campaigns").exists()


def test_a_linked_campaign_ledger_is_never_written_through(library, tmp_path):
    store, authority, locker = library
    outside = tmp_path / "outside"
    outside.mkdir()
    (store.root / "authority" / "campaigns").mkdir()
    make_dir_link(store.root / "authority" / "campaigns" / "c1", outside)
    with pytest.raises((AuthorityUnavailable, LedgerBroken, LinkRefused)):
        SovereigntyLedger(authority).record("c1", ControlChange("c1", "wren", P1),
                                            authority.handle_for(OWNER, trusted=True))
    assert list(outside.iterdir()) == []
