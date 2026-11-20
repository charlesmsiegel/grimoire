"""The pure 077 commitment values, fold and codec. Synthetic names only; nothing reads files or calls a model."""

from __future__ import annotations

import pytest

from grimoire.commitments import (
    Authorship, ClockTime, Commitment, CommitmentError, CommitmentLedger, DeadlineState, Offer, Transition, Void,
    fold, fold_one, ledger_from_payload, ledger_to_payload, transition_fingerprint, void_fingerprint,
)
from grimoire.core import DIRECTOR, InstanceRef, Moment

C = "c"
WREN, MARA, TAM = (InstanceRef(C, "character", name) for name in ("wren", "mara", "tam"))
DUE = DeadlineState("dl-tower", True, ClockTime(10, 0), "New moon", False)
PASSED = DeadlineState("dl-tower", True, ClockTime(10, 0), "New moon", True, "weeks")


def mission(**changes) -> Commitment:
    fields = dict(campaign_id=C, commitment_id="m-tower", kind="mission", summary="Clear the signal tower.",
                  owed_by=(WREN,), owed_to=(TAM,), deadline_id="dl-tower")
    fields.update(changes)
    return Commitment(**fields)


def at(sequence: int, scene: str = "s1") -> Moment:
    return Moment(sequence, scene, f"t{sequence}")


def backing(status: str, cites, *, turn: int = 2, note: str = "", cid: str = "c-wren") -> Authorship:
    return Authorship(cid, "wren", "human:ana", "commitment", "s1", f"t{turn}", "I will not take it.",
                      transition_fingerprint(C, "m-tower", status, cites, note))


def offer(op: str = "op-1", cites=("e-ask",), sequence: int = 1, **changes) -> Offer:
    return Offer(op, mission(**changes), cites, at(sequence), ClockTime(1, 720))


def decide(status: str, op: str = "op-2", cites=("e-no",), sequence: int = 2, *, pc: bool = True) -> Transition:
    if not pc:
        return Transition(op, "m-tower", status, cites, at(sequence), controls=(("wren", DIRECTOR),))
    return Transition(op, "m-tower", status, cites, at(sequence), controls=(("wren", "human:ana"),),
                      authorship=(backing(status, cites, turn=sequence, cid="c-wren" if op == "op-2" else f"c-{op}"),))


def outcome(status: str, op: str, cites, sequence: int, clock: ClockTime | None = None) -> Transition:
    return Transition(op, "m-tower", status, cites, at(sequence), clock_at=clock)


def ledger(*ops) -> CommitmentLedger:
    return CommitmentLedger(C, ops)


def supported(*ids: str, **overrides: str) -> dict[str, str]:
    values = {event_id: "supported" for event_id in ids}
    values.update(overrides)
    return values


def reason(call) -> str:
    with pytest.raises(CommitmentError) as caught:
        call()
    return caught.value.reason


# -- values ---------------------------------------------------------------------------------------

def test_a_commitment_is_canonical_and_never_owed_to_oneself():
    a = mission(owed_by=(WREN, MARA))
    assert a == mission(owed_by=(MARA, WREN)) and a.owed_by == (MARA, WREN)
    assert reason(lambda: mission(owed_to=(WREN,))) == "self_owed"
    assert reason(lambda: mission(owed_by=(InstanceRef("other", "character", "wren"),))) == "campaign_mismatch"
    assert reason(lambda: mission(kind="vow")) == "invalid_record"
    assert reason(lambda: mission(owed_by=())) == "invalid_record"
    assert reason(lambda: mission(summary="  ")) == "invalid_record"


def test_every_step_cites_events_and_names_its_moment():
    assert reason(lambda: offer(cites=())) == "no_citation"
    assert reason(lambda: offer(cites=("e-1", "e-1"))) == "invalid_record"
    assert offer(cites=("e-2", "e-1")).cites == ("e-1", "e-2")
    assert reason(lambda: Offer("op-1", mission(), ("e-1",), 3)) == "invalid_record"
    assert reason(lambda: Offer("op-1", mission(deadline_id=None), ("e-1",), at(1), schedules=True)) == "no_deadline_id"
    assert reason(lambda: Offer("x" * 101, mission(), ("e-1",), at(1))) == "invalid_record"
    assert reason(lambda: ClockTime(0)) == "invalid_record" and reason(lambda: ClockTime(1, 1440)) == "invalid_record"


def test_decisions_are_backed_by_every_player_obligor_and_outcomes_by_nobody():
    assert reason(lambda: Transition("op-2", "m-tower", "refused", ("e-no",), at(2))) == "no_controls"
    assert reason(lambda: Transition("op-2", "m-tower", "refused", ("e-no",), at(2),
                                     controls=(("wren", "human:ana"),))) == "pc_authorship"
    other_turn = Authorship("c-wren", "wren", "human:ana", "commitment", "s1", "t9", "No.", "sha256:" + "0" * 64)
    assert reason(lambda: Transition("op-2", "m-tower", "refused", ("e-no",), at(2), controls=(("wren", "human:ana"),),
                                     authorship=(other_turn,))) == "pc_authorship"
    assert reason(lambda: Transition("op-3", "m-tower", "fulfilled", ("e-done",), at(3),
                                     controls=(("wren", DIRECTOR),))) == "unexpected_backing"
    assert reason(lambda: Transition("op-3", "m-tower", "offered", ("e-done",), at(3))) == "invalid_record"
    assert decide("refused", pc=False).authorship == ()


def test_the_ledger_refuses_what_a_reader_could_not_trust():
    assert reason(lambda: ledger(offer(), offer())) == "duplicate_operation"
    assert reason(lambda: ledger(offer(), offer("op-2"))) == "duplicate_commitment"
    assert reason(lambda: ledger(decide("accepted", pc=False))) == "unknown_commitment"
    mara_decides = Transition("op-2", "m-tower", "refused", ("e-no",), at(2), controls=(("mara", DIRECTOR),))
    assert reason(lambda: ledger(offer(), mara_decides)) == "not_obligor"
    assert reason(lambda: ledger(offer(), Void("op-2", "op-9", "typo"))) == "invalid_void"
    assert reason(lambda: ledger(offer(), Void("op-2", "op-1", "typo"), Void("op-3", "op-1", "again"))) == "invalid_void"
    unbacked_void = Void("op-3", "op-2", "wrong scene")
    assert reason(lambda: ledger(offer(), decide("refused"), unbacked_void)) == "pc_authorship"
    assert reason(lambda: CommitmentLedger(C, (Offer("op-1", mission(campaign_id="d"), ("e-1",), at(1)),))) \
        == "campaign_mismatch"


def test_fingerprints_pin_the_exact_step():
    base = transition_fingerprint(C, "m-tower", "refused", ("e-1", "e-2"))
    assert base == transition_fingerprint(C, "m-tower", "refused", ("e-2", "e-1"))
    assert len({base, transition_fingerprint(C, "m-tower", "accepted", ("e-1", "e-2")),
                transition_fingerprint(C, "m-tower", "refused", ("e-1",)),
                transition_fingerprint(C, "m-tower", "refused", ("e-1", "e-2"), "late"),
                transition_fingerprint("d", "m-tower", "refused", ("e-1", "e-2"))}) == 5
    # A note cannot pass for a citation, nor a citation for a note.
    assert transition_fingerprint(C, "m", "refused", ("e-1",), "e-2") != transition_fingerprint(C, "m", "refused",
                                                                                                ("e-1", "e-2"))
    assert void_fingerprint(C, "op-2", "typo") != void_fingerprint(C, "op-2", "typo ")


# -- fold -----------------------------------------------------------------------------------------

def test_a_refusal_closes_the_commitment_and_keeps_its_deadline():
    record = ledger(offer(), decide("refused"))
    state = fold_one(record, "m-tower", supported("e-ask", "e-no"), {"dl-tower": PASSED})
    assert (state.status, state.certain, state.moment) == ("refused", True, at(2))
    assert state.deadline == PASSED and not state.overdue and state.late is None
    assert [step.outcome for step in state.steps] == ["applied"]
    assert fold_one(record, "m-tower", supported("e-ask", "e-no"), {}).deadline == DeadlineState("dl-tower", False)


def test_an_open_commitment_past_its_deadline_is_overdue_and_a_late_fulfilment_says_so():
    accepted = ledger(offer(), decide("accepted"))
    assert fold_one(accepted, "m-tower", supported("e-ask", "e-no"), {"dl-tower": PASSED}).overdue
    assert not fold_one(accepted, "m-tower", supported("e-ask", "e-no"), {"dl-tower": DUE}).overdue
    done = ledger(offer(), decide("accepted"), outcome("fulfilled", "op-3", ("e-done",), 3, ClockTime(12, 0)))
    state = fold_one(done, "m-tower", supported("e-ask", "e-no", "e-done"), {"dl-tower": PASSED})
    assert state.status == "fulfilled" and not state.overdue and state.late is True
    on_time = ledger(offer(), decide("accepted"), outcome("fulfilled", "op-3", ("e-done",), 3, ClockTime(9, 0)))
    assert fold_one(on_time, "m-tower", supported("e-ask", "e-no", "e-done"), {"dl-tower": DUE}).late is False
    unclocked = ledger(offer(), decide("accepted"), outcome("fulfilled", "op-3", ("e-done",), 3))
    assert fold_one(unclocked, "m-tower", supported("e-ask", "e-no", "e-done"), {"dl-tower": DUE}).late is None


def test_a_retcon_skips_the_step_and_whatever_only_followed_from_it():
    record = ledger(offer(), decide("accepted"), outcome("partially_fulfilled", "op-3", ("e-half",), 3),
                    outcome("fulfilled", "op-4", ("e-done",), 4))
    state = fold_one(record, "m-tower", supported("e-ask", "e-half", "e-done", **{"e-no": "revoked"}), {})
    assert state.status == "offered" and state.certain
    assert [s.outcome for s in state.steps] == ["unsupported", "stranded", "stranded"]
    undone_offer = fold_one(record, "m-tower", supported("e-no", "e-half", "e-done", **{"e-ask": "revoked"}), {})
    assert undone_offer.status == "unsupported" and [s.outcome for s in undone_offer.steps] == ["stranded"] * 3


def test_a_step_still_syncing_is_uncertainty_never_a_skip():
    record = ledger(offer(), decide("accepted"), outcome("fulfilled", "op-3", ("e-done",), 3))
    for value in ("missing", "unreadable"):
        state = fold_one(record, "m-tower", supported("e-ask", "e-done", **{"e-no": value}), {})
        assert state.status == "fulfilled" and not state.certain
        assert [s.outcome for s in state.steps] == ["applied", "applied"]
    assert fold_one(record, "m-tower", supported("e-ask", "e-done"), {}).certain is False  # an id not supplied


def test_a_void_withdraws_an_error_and_the_rest_refolds():
    reused = Void("op-3", "op-2", "wrong mission", (backing("refused", ("e-no",)),))
    assert reason(lambda: ledger(offer(), decide("refused"), reused)) == "pc_authorship"  # endorsed the refusal
    misbacked = Transition("op-2", "m-tower", "refused", ("e-no",), at(2), controls=(("wren", "human:ana"),),
                           authorship=(backing("accepted", ("e-no",)),))
    assert reason(lambda: ledger(offer(), misbacked)) == "pc_authorship"  # endorsed another status
    void = Void("op-3", "op-2", "wrong mission", (Authorship("c-wren", "wren", "human:ana", "commitment", "s1", "t2",
                                                             "I will not take it.", void_fingerprint(C, "op-2", "wrong mission")),))
    record = ledger(offer(), decide("refused"), void, decide("accepted", "op-4", ("e-yes",), 4))
    state = fold_one(record, "m-tower", supported("e-ask", "e-yes", **{"e-no": "missing"}), {})
    assert state.status == "accepted" and state.certain  # a voided step's support no longer matters
    assert [s.outcome for s in state.steps] == ["voided", "applied"]
    withdrawn = ledger(offer(), Void("op-2", "op-1", "never offered"))
    assert fold_one(withdrawn, "m-tower", {}, {}).status == "voided"


def test_a_dead_history_stays_certain_and_a_backing_counts_once():
    revoked = fold_one(ledger(offer()), "m-tower", {"e-ask": "revoked"}, {})
    assert (revoked.status, revoked.certain) == ("unsupported", True)  # a confirmed retcon is not doubt
    refused = ledger(offer(), decide("refused"), outcome("lapsed", "op-3", ("e-late",), 3))
    state = fold_one(refused, "m-tower", supported("e-ask", "e-no", **{"e-late": "missing"}), {})
    assert (state.status, state.certain) == ("refused", True)  # a step that cannot follow adds no doubt
    voided = ledger(offer(), Void("op-2", "op-1", "never offered"), decide("accepted", "op-3", ("e-yes",), 3))
    assert fold_one(voided, "m-tower", supported("e-ask", **{"e-yes": "unreadable"}), {}).certain
    twice = Authorship("c-wren", "wren", "human:ana", "commitment", "s1", "t2", "No.",
                       void_fingerprint(C, "op-2", "wrong"))
    assert reason(lambda: ledger(offer(), decide("refused"), Void("op-3", "op-2", "wrong", (twice, twice)))) \
        == "pc_authorship"
    assert reason(lambda: Transition("op-2", "m-tower", "refused", ("e-no",), at(2), controls=(("wren", "human:ana"),),
                                     authorship=(backing("refused", ("e-no",)),) * 2)) == "pc_authorship"


def test_a_void_cannot_be_used_to_replay_or_skip_a_players_consent():
    void = Void("op-3", "op-2", "wrong", (Authorship("c-wren", "wren", "human:ana", "commitment", "s1", "t2", "No.",
                                                      void_fingerprint(C, "op-2", "wrong")),))
    replay = Transition("op-4", "m-tower", "accepted", ("e-no",), at(4), controls=(("wren", "human:ana"),),
                        authorship=(backing("accepted", ("e-no",), turn=4),))
    assert reason(lambda: ledger(offer(), decide("refused"), void, replay)) == "reused_contribution"
    record = ledger(offer(), decide("refused"))
    assert record.backers("op-1") == record.backers("op-2") == decide("refused").authorship
    assert reason(lambda: ledger(offer(), decide("refused"), Void("op-3", "op-1", "never offered"))) \
        == "pc_authorship"  # withdrawing the offer withdraws Wren's refusal, so it needs Wren's player
    consented = Void("op-3", "op-1", "never offered",
                     (Authorship("c-wren", "wren", "human:ana", "commitment", "s1", "t2", "No.",
                                 void_fingerprint(C, "op-1", "never offered")),))
    assert fold_one(ledger(offer(), decide("refused"), consented), "m-tower", {}, {}).status == "voided"


def test_as_of_reads_the_commitment_as_it_stood_then():
    record = ledger(offer(sequence=3), decide("refused", sequence=5))
    support = supported("e-ask", "e-no")
    assert fold_one(record, "m-tower", support, {}, as_of=2) is None
    then = fold_one(record, "m-tower", support, {}, as_of=4)
    assert (then.status, then.as_of) == ("offered", 4)
    assert fold(record, support, {}, as_of=5)[0].status == "refused"
    assert fold(None, {}, {}) == ()
    early = ledger(offer(sequence=3), decide("accepted", sequence=5), outcome("fulfilled", "op-3", ("e-done",), 4))
    assert [s.outcome for s in fold_one(early, "m-tower", supported("e-ask", "e-no", "e-done"), {}).steps] \
        == ["applied", "stranded"]


# -- codec ----------------------------------------------------------------------------------------

def test_the_payload_round_trips_and_fails_closed():
    void = Void("op-5", "op-4", "duplicate", ())
    record = ledger(offer(), decide("accepted"), outcome("partially_fulfilled", "op-3", ("e-half",), 3, ClockTime(5, 30)),
                    outcome("lapsed", "op-4", ("e-gone",), 4), void)
    payload = ledger_to_payload(record)
    assert ledger_from_payload(C, payload) == record
    frozen = {"schema": payload["schema"], "operations": tuple(payload["operations"])}
    assert ledger_from_payload(C, frozen) == record
    assert reason(lambda: ledger_from_payload(C, {**payload, "schema": "grimoire.commitments/9"})) == "unknown_schema"
    extra = dict(payload["operations"][0], extra=1)
    assert reason(lambda: ledger_from_payload(C, {**payload, "operations": [extra]})) == "invalid_record"
    wrong = dict(payload["operations"][1], status=7)
    assert reason(lambda: ledger_from_payload(C, {**payload, "operations": [payload["operations"][0], wrong]})) \
        == "invalid_record"
    assert reason(lambda: ledger_from_payload(C, {**payload, "operations": [{"op": "erase"}]})) == "invalid_record"
    first = payload["operations"][0]
    null_scene = dict(first, at={**first["at"], "scene_id": None})
    assert reason(lambda: ledger_from_payload(C, {**payload, "operations": [null_scene]})) == "invalid_record"
