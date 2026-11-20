"""077's proving scenario, persisted: a refused mission keeps its refusal and deadline, and later narration that
claims the mission was done is caught by the referee. Synthetic names; nothing calls a model.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from grimoire.commitments import CommitmentError
from grimoire.commitmentstore import Due
from grimoire.core import DIRECTOR
from grimoire.referee import ContinuityClaim, Facts, RefereeError, referee
from grimoire.store import LibraryStore

from commitment_fixtures import (
    C, CONTROLS, SCOPE, TAM, WREN, INES, Handles, advance, campaign, commitments, endorse, etag, event, mission, say,
)


def claim(text: str, quote: str, commitment_id: str = "m-tower", *, claim_id: str = "k1", actor=WREN,
          mode: str = "literal") -> ContinuityClaim:
    start = text.index(quote)
    return ContinuityClaim(C, claim_id, "fulfills", actor, (start, start + len(quote)), quote,
                           commitment_id=commitment_id, mode=mode)


def facts_at(library, as_of: int) -> Facts:
    return Facts(C, as_of, commitments=library.read(SCOPE, requested_by=DIRECTOR, as_of=as_of).commitments)


@pytest.fixture
def refused(tmp_path):
    """Scene s1: Tam offers the tower mission, due on the new moon. Scene s2: Wren's player refuses it.
    Scene s3 opens three weeks later, past the deadline."""
    handles = Handles()
    root = tmp_path / "lib"
    with LibraryStore.create(root) as store:
        library = campaign(store, handles)
        event(store, handles, "e-ask", 1, scene="s1", turn="t1", summary="Tam asks the party to clear the tower.")
        library.offer(SCOPE, "op-ask", mission(), cites=["e-ask"], expected=etag(library),
                      due=Due("2024-03-01", "New moon")).commit()
        event(store, handles, "e-no", 4, scene="s2", turn="t4", summary="Wren tells Tam to find someone else.")
        library.record(SCOPE, "op-no", "m-tower", "refused", cites=["e-no"], expected=etag(library),
                       controls=CONTROLS, contributions=[say(scene="s2", turn="t4")],
                       endorsements=[endorse(handles, "m-tower", "refused", ["e-no"])]).commit()
        advance(store, handles, "three-weeks", 21 * 1440)
        event(store, handles, "e-later", 9, scene="s3", turn="t9", summary="The party reaches the river ford.")
    with LibraryStore.open(root) as store:  # later scenes read the record from disk, not from memory
        yield store, handles, commitments(store, handles)


def test_the_refusal_and_its_deadline_stay_on_the_record(refused):
    store, handles, library = refused
    state = library.read(SCOPE, requested_by=DIRECTOR).commitment("m-tower")
    assert state.status == "refused" and state.certain
    assert state.deadline.found and state.deadline.passed and state.deadline.triggered_by == "three-weeks"
    assert not state.overdue  # a refused mission is not late: nobody owes it any more
    with pytest.raises(CommitmentError) as caught:
        library.record(SCOPE, "op-done", "m-tower", "fulfilled", cites=["e-later"], expected=etag(library))
    assert caught.value.reason == "illegal_transition"
    before_refusal = library.read(SCOPE, requested_by=DIRECTOR, as_of=3).commitment("m-tower")
    assert before_refusal.status == "offered"


def test_later_narration_that_claims_the_mission_done_is_a_contradiction(refused):
    store, handles, library = refused
    text = "At the ford Wren boasts that the tower lies cleared, the job done as promised."
    report = referee(text, facts=facts_at(library, 9), claims=[claim(text, "the tower lies cleared")])
    (finding,) = report.material
    assert (finding.category, finding.reason) == ("outcome", "contradicts_commitment")
    assert finding.evidence[0].detail == "status=refused"
    assert {e.ref for e in finding.evidence} >= {"operation:op-ask", "operation:op-no"}


def test_figurative_unknown_and_stale_readings_are_never_material(refused):
    store, handles, library = refused
    text = "Wren's patience was a tower she had long since cleared."
    figurative = referee(text, facts=facts_at(library, 9),
                         claims=[claim(text, "a tower she had long since cleared", mode="figurative")])
    assert figurative.material == () and figurative.findings[0].classification == "metaphor"
    unknown = referee(text, facts=facts_at(library, 9), claims=[claim(text, "long since cleared", "m-ghost")])
    assert unknown.unchecked == (("k1", "no_commitment"),)
    stale = library.read(SCOPE, requested_by=DIRECTOR, as_of=4).commitments
    with pytest.raises(RefereeError) as caught:
        Facts(C, 9, commitments=stale)
    assert caught.value.reason == "stale_commitment"
    relabelled = [replace(state, as_of=3) for state in library.read(SCOPE, requested_by=DIRECTOR, as_of=9).commitments]
    with pytest.raises(RefereeError) as caught:
        Facts(C, 3, commitments=relabelled)  # the refusal at sequence 4 had not happened by 3
    assert caught.value.reason == "stale_commitment"


def test_an_accepted_promise_runs_overdue_and_only_its_fulfilment_clears_the_claim(tmp_path):
    handles = Handles()
    with LibraryStore.create(tmp_path / "lib") as store:
        library = campaign(store, handles)
        event(store, handles, "e-vow", 1, scene="s1", turn="t1", summary="Ines swears to bring Tam a boat.")
        promise = mission("p-boat", owed_by=(INES,), owed_to=(TAM,), kind="promise", deadline_id="dl-boat",
                          summary="Bring Tam a boat by the spring fair.")
        library.offer(SCOPE, "op-vow", promise, cites=["e-vow"], expected=etag(library),
                      due=Due("2024-02-25", "Spring fair")).commit()
        library.record(SCOPE, "op-yes", "p-boat", "accepted", cites=["e-vow"], expected=etag(library),
                       controls=CONTROLS).commit()
        advance(store, handles, "a-week", 7 * 1440)
        event(store, handles, "e-ford", 5, scene="s2", turn="t5", summary="Ines arrives at the ford on foot.")
        state = library.read(SCOPE, requested_by=DIRECTOR).commitment("p-boat")
        assert state.status == "accepted" and state.overdue
        text = "Ines rows up in the boat she promised Tam."
        early = referee(text, facts=facts_at(library, 5), claims=[claim(text, "the boat she promised", "p-boat",
                                                                        actor=INES)])
        assert [f.reason for f in early.material] == ["not_fulfilled"]
        event(store, handles, "e-boat", 6, scene="s2", turn="t6", summary="Ines hands Tam a boat at the ford.")
        library.record(SCOPE, "op-boat", "p-boat", "fulfilled", cites=["e-boat"], expected=etag(library)).commit()
        done = library.read(SCOPE, requested_by=DIRECTOR).commitment("p-boat")
        assert done.status == "fulfilled" and not done.overdue and done.late is True
        later = referee(text, facts=facts_at(library, 6), claims=[claim(text, "the boat she promised", "p-boat",
                                                                        actor=INES)])
        assert later.clean == ("k1",) and later.findings == ()
        someone_else = referee(text, facts=facts_at(library, 6),
                               claims=[claim(text, "the boat she promised", "p-boat", actor=WREN)])
        assert [(f.classification, f.reason) for f in someone_else.findings] == [("uncertain", "not_obligor")]


def test_a_refusal_still_syncing_makes_the_claim_uncertain_not_material(refused):
    store, handles, library = refused
    next(p for p in store.root.rglob("*.json") if "e-no" in p.name).unlink()  # not on this device yet
    text = "Wren says the tower lies cleared."
    report = referee(text, facts=facts_at(library, 9), claims=[claim(text, "the tower lies cleared")])
    assert report.material == ()
    assert [(f.classification, f.reason) for f in report.findings] == [("uncertain", "contradicts_commitment")]
