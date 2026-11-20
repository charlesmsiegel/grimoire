"""077's store adapter: offers, steps and voids as one authorized scope write beside 055's calendar.

Synthetic campaign ``c`` (see ``commitment_fixtures``); every refusal leaves the store as it was.
"""

from __future__ import annotations

import pytest

from grimoire.calendarstore import CalendarLibrary
from grimoire.commitments import CommitmentError
from grimoire.commitmentstore import CommitmentLibrary, Due, read_support
from grimoire.core import DIRECTOR, Event, InstanceRef, Moment
from grimoire.core.authority import AuthorityError
from grimoire.core.model import ControlAssignment
from grimoire.effects import accepted_token, as_knowledge_event
from grimoire.effectstore import Endorsement
from grimoire.evidencestore import EvidenceLibrary
from grimoire.store import LibraryStore, StaleWrite
from grimoire.store import etag as etag_of
from grimoire.sync import resolve, sync
from grimoire.store.layout import address_parts
from grimoire.testing.fileops import snapshot

from commitment_fixtures import (
    ANA, C, CONTROLS, INES, SCOPE, TAM, WREN, Handles, advance, campaign, commitments, endorse, endorse_void, etag,
    event, mission, say,
)
from effects_fixtures import contribution, effects_library, give
from store_helpers import OWNER
from sync_helpers import DESKTOP, PHONE, kept, make_pair
from sync_helpers import handles as sync_handles

TOWER = Due("2024-03-01", "New moon", 360)


@pytest.fixture
def world(tmp_path):
    handles = Handles()
    with LibraryStore.create(tmp_path / "lib") as store:
        library = campaign(store, handles)
        event(store, handles, "e-ask", 1, turn="t1", summary="Tam offers the party the tower job.")
        event(store, handles, "e-no", 2, turn="t2", summary="Wren turns the tower job down.")
        yield store, handles, library


def reason(call) -> str:
    with pytest.raises(CommitmentError) as caught:
        call()
    return caught.value.reason


def scope_bytes(store) -> bytes:
    return (store.root / "/".join(address_parts(SCOPE))).read_bytes()


def offered(library, commitment=None, *, op="op-1", cites=("e-ask",), due=TOWER):
    return library.offer(SCOPE, op, commitment or mission(), cites=cites, expected=etag(library), due=due).commit()


def refuse(library, handles, *, op="op-2", cites=("e-no",), **kwargs):
    return library.record(SCOPE, op, "m-tower", "refused", cites=cites, expected=etag(library), controls=CONTROLS,
                          contributions=[say()], endorsements=[endorse(handles, "m-tower", "refused", cites)], **kwargs)


# -- offers and deadlines -------------------------------------------------------------------------

def test_an_offer_and_its_deadline_land_in_one_scope_write(world):
    store, handles, library = world
    before = store.read(SCOPE).record.payload
    change = library.offer(SCOPE, "op-1", mission(), cites=["e-ask"], expected=etag(library), due=TOWER)
    assert sorted(change.preview()) == [f"campaigns/{C}/scope.json"]
    view = change.commit()
    state = view.commitment("m-tower")
    assert (state.status, state.certain, state.moment.sequence) == ("offered", True, 1)
    assert state.deadline.found and state.deadline.label == "New moon" and state.deadline.passed is False
    assert state.offer.operation.clock_at is not None and state.offer.operation.schedules
    calendar = CalendarLibrary(store, handles.app).read(SCOPE).state
    assert [d.deadline_id for d in calendar.deadlines] == ["dl-tower"]
    assert calendar.receipt("commitment-op-1") is not None
    after = store.read(SCOPE).record.payload
    assert {k: v for k, v in after.items() if k not in ("commitments", "calendar")} == \
        {k: v for k, v in before.items() if k not in ("commitments", "calendar")}


def test_retries_are_no_ops_and_a_reused_id_or_stale_read_is_refused(world):
    store, handles, library = world
    stale = etag(library)
    offered(library)
    written = scope_bytes(store)
    retry = library.offer(SCOPE, "op-1", mission(), cites=["e-ask"], expected=stale, due=TOWER)
    assert retry.already_applied and retry.preview() == {}
    assert retry.commit().commitment("m-tower").status == "offered" and scope_bytes(store) == written
    assert reason(lambda: library.offer(SCOPE, "op-1", mission(summary="Something else."), cites=["e-ask"],
                                        expected=etag(library), due=TOWER)) == "operation_mismatch"
    with pytest.raises(StaleWrite):
        library.offer(SCOPE, "op-9", mission("m-other", deadline_id=None), cites=["e-ask"], expected=stale)
    assert scope_bytes(store) == written


def test_an_offer_needs_known_parties_supported_citations_and_a_real_deadline(world):
    store, handles, library = world
    untouched = snapshot(store.root)
    nobody = InstanceRef(C, "character", "nobody")
    assert reason(lambda: library.offer(SCOPE, "op-1", mission(owed_to=(nobody,)), cites=["e-ask"],
                                        expected=etag(library), due=TOWER)) == "unknown_party"
    assert reason(lambda: library.offer(SCOPE, "op-1", mission(), cites=["e-later"], expected=etag(library),
                                        due=TOWER)) == "citation_missing"
    assert reason(lambda: library.offer(SCOPE, "op-1", mission(deadline_id=None), cites=["e-ask"],
                                        expected=etag(library), due=TOWER)) == "no_deadline_id"
    assert reason(lambda: library.offer(SCOPE, "op-1", mission(), cites=["e-ask"],
                                        expected=etag(library))) == "unknown_deadline"
    assert reason(lambda: library.offer(SCOPE, "op-1", mission(), cites=["e-ask"], expected=etag(library),
                                        due=Due("2024-02-31", "Never"))) == "invalid_field"
    assert snapshot(store.root) == untouched
    offered(library)
    assert reason(lambda: offered(library, op="op-5")) == "duplicate_commitment"


def test_a_deadline_already_in_the_calendar_is_linked_and_a_colliding_schedule_refused(world):
    store, handles, library = world
    clock = CalendarLibrary(store, handles.app)
    clock.schedule(SCOPE, "commitment-op-7", "dl-gate", "2024-03-05", label="Gate closes",
                   expected=clock.read(SCOPE).etag).commit()
    assert reason(lambda: library.offer(SCOPE, "op-7", mission(deadline_id="dl-bridge"), cites=["e-ask"],
                                        expected=etag(library), due=TOWER)) == "operation_mismatch"
    linked = offered(library, mission(deadline_id="dl-gate"), due=None).commitment("m-tower")
    assert linked.deadline.found and linked.deadline.label == "Gate closes"
    assert not linked.offer.operation.schedules


def test_without_a_calendar_a_commitment_has_no_deadline_or_clock(tmp_path):
    handles = Handles()
    with LibraryStore.create(tmp_path / "lib") as store:
        library = campaign(store, handles, calendar=False)
        event(store, handles, "e-ask", 1, turn="t1")
        assert reason(lambda: library.offer(SCOPE, "op-1", mission(), cites=["e-ask"], expected=etag(library),
                                            due=TOWER)) == "uninitialized"
        state = offered(library, mission(deadline_id=None), due=None).commitment("m-tower")
        assert state.deadline is None and state.offer.operation.clock_at is None
        assert "calendar" not in store.read(SCOPE).record.payload


# -- steps ----------------------------------------------------------------------------------------

def test_a_player_characters_refusal_needs_their_own_words_and_endorsement(world):
    store, handles, library = world
    offered(library)
    untouched = scope_bytes(store)
    plain = dict(cites=["e-no"], expected=etag(library), controls=CONTROLS)
    assert reason(lambda: library.record(SCOPE, "op-2", "m-tower", "refused", **plain)) == "pc_authorship"
    assert reason(lambda: library.record(SCOPE, "op-2", "m-tower", "refused", contributions=[say()],
                                         **plain)) == "not_endorsed"
    assert reason(lambda: library.record(SCOPE, "op-2", "m-tower", "refused", contributions=[say()],
                                         endorsements=[endorse(handles, "m-tower", "accepted", ["e-no"])],
                                         **plain)) == "invalid_endorsement"
    assert reason(lambda: library.record(SCOPE, "op-2", "m-tower", "refused", contributions=[say(turn="t9")],
                                         endorsements=[endorse(handles, "m-tower", "refused", ["e-no"])],
                                         **plain)) == "pc_authorship"
    narrated = contribution("c-wren", turn="t2", scene="s1", kind="interiority", text="Wren hesitates.")
    assert reason(lambda: library.record(SCOPE, "op-2", "m-tower", "refused", contributions=[narrated],
                                         endorsements=[endorse(handles, "m-tower", "refused", ["e-no"])],
                                         **plain)) == "pc_authorship"
    bob = Endorsement("c-wren", handles.player("human:bob"),
                      endorse(handles, "m-tower", "refused", ["e-no"]).fingerprint)
    assert reason(lambda: library.record(SCOPE, "op-2", "m-tower", "refused", contributions=[say()],
                                         endorsements=[bob], **plain)) == "invalid_endorsement"
    assert scope_bytes(store) == untouched
    state = refuse(library, handles).commit().commitment("m-tower")
    assert state.status == "refused" and state.steps[0].operation.controls == (("wren", ANA),)
    assert state.steps[0].operation.authorship[0].text == "I will not take the tower job."


def test_one_player_never_decides_for_a_party_that_includes_another(world):
    store, handles, library = world
    offered(library, mission(owed_by=(WREN, INES)))
    controls = CONTROLS + (ControlAssignment(C, "ines", "human:bob"),)
    assert reason(lambda: library.record(SCOPE, "op-2", "m-tower", "accepted", cites=["e-no"],
                                         expected=etag(library), controls=controls, contributions=[say()],
                                         endorsements=[endorse(handles, "m-tower", "accepted", ["e-no"])])) \
        == "pc_authorship"
    ines = say("c-ines", actor_id="ines", by="human:bob", text="I will go, if Wren will.")
    view = library.record(SCOPE, "op-2", "m-tower", "accepted", cites=["e-no"], expected=etag(library),
                          controls=controls, contributions=[say(), ines],
                          endorsements=[endorse(handles, "m-tower", "accepted", ["e-no"]),
                                        endorse(handles, "m-tower", "accepted", ["e-no"], cid="c-ines",
                                                principal="human:bob")]).commit()
    assert view.commitment("m-tower").status == "accepted"


def test_the_director_decides_for_npcs_and_adjudicates_outcomes(world):
    store, handles, library = world
    event(store, handles, "e-ines", 3, turn="t3", summary="Ines promises Tam a boat.")
    event(store, handles, "e-boat", 4, turn="t4", summary="Ines hands Tam a boat.")
    offered(library, mission("p-boat", owed_by=(INES,), kind="promise", deadline_id=None), op="op-p", cites=["e-ines"],
            due=None)
    assert reason(lambda: library.record(SCOPE, "op-q", "p-boat", "accepted", cites=["e-ines"],
                                         expected=etag(library), controls=CONTROLS, contributions=[say()])) \
        == "unexpected_backing"
    library.record(SCOPE, "op-q", "p-boat", "accepted", cites=["e-ines"], expected=etag(library),
                   controls=CONTROLS).commit()
    assert reason(lambda: library.record(SCOPE, "op-r", "p-boat", "fulfilled", cites=["e-boat"],
                                         expected=etag(library), controls=CONTROLS)) == "unexpected_backing"
    state = library.record(SCOPE, "op-r", "p-boat", "fulfilled", cites=["e-boat"], expected=etag(library)).commit()
    assert state.commitment("p-boat").status == "fulfilled"
    assert state.commitment("p-boat").steps[0].operation.controls == (("ines", DIRECTOR),)


def test_steps_follow_the_commitments_course_in_order(world):
    store, handles, library = world
    event(store, handles, "e-done", 5, turn="t5", summary="Wren lights the tower beacon.")
    offered(library, cites=("e-no",))
    assert reason(lambda: library.record(SCOPE, "op-2", "m-tower", "fulfilled", cites=["e-done"],
                                         expected=etag(library))) == "illegal_transition"
    assert reason(lambda: library.record(SCOPE, "op-2", "m-ghost", "fulfilled", cites=["e-done"],
                                         expected=etag(library))) == "unknown_commitment"
    assert reason(lambda: library.record(SCOPE, "op-2", "m-tower", "lapsed", cites=["e-ask"],
                                         expected=etag(library))) == "out_of_order"
    library.record(SCOPE, "op-2", "m-tower", "lapsed", cites=["e-done"], expected=etag(library)).commit()
    assert reason(lambda: library.record(SCOPE, "op-3", "m-tower", "fulfilled", cites=["e-done"],
                                         expected=etag(library))) == "illegal_transition"


def test_reading_is_the_directors_and_the_keys_must_be_issued(world):
    store, handles, library = world
    with pytest.raises(AuthorityError):
        library.read(SCOPE, requested_by="human:ana")
    with pytest.raises(AuthorityError):
        CommitmentLibrary(store, handles.authorizer.handle_for(OWNER, trusted=False), issued=handles.authorizer.issued)
    with pytest.raises(AuthorityError):
        CommitmentLibrary(store, type("Fake", (), {"trusted": True})(), issued=handles.authorizer.issued)
    before = snapshot(store.root)
    assert library.read(SCOPE, requested_by=DIRECTOR).commitments == ()
    assert snapshot(store.root) == before


# -- retcons, uncertainty and voids ---------------------------------------------------------------

def _accept_and_bridge(store, handles):
    effects = effects_library(store, handles)
    proposal = give(sequence=6, turn="t6", summary="Wren hands Tam the tower key.")
    done = effects.accept(proposal, controls=CONTROLS, contributions=[contribution("c7", turn="t6")],
                          endorsements=[handles.endorse(proposal, "c7")]).commit()
    accepted, _ = effects.read(done.ref, requested_by=DIRECTOR)
    EvidenceLibrary(store, handles.app, owner=OWNER).append(C, [as_knowledge_event(accepted)]).commit()
    return effects, done.ref, proposal


def _revoke(effects, handles, ref, proposal):
    _, tag = effects.read(ref, requested_by=DIRECTOR)
    effects.revoke(ref, reason="Ana rerolled the turn", expected=tag,
                   endorsements=[handles.undo(ref, proposal)]).commit()


def test_a_retcon_of_the_cited_fiction_unwinds_the_step(world):
    store, handles, library = world
    offered(library, mission(owed_by=(TAM,), owed_to=(WREN,), kind="promise"), cites=("e-ask",))
    effects, ref, proposal = _accept_and_bridge(store, handles)
    library.record(SCOPE, "op-2", "m-tower", "accepted", cites=[ref.event_id], expected=etag(library),
                   controls=CONTROLS).commit()
    assert library.read(SCOPE, requested_by=DIRECTOR).commitment("m-tower").status == "accepted"
    _revoke(effects, handles, ref, proposal)
    state = library.read(SCOPE, requested_by=DIRECTOR).commitment("m-tower")
    assert state.status == "offered" and state.steps[0].outcome == "unsupported"
    assert state.steps[0].cited == ((ref.event_id, "revoked"),)
    assert reason(lambda: library.record(SCOPE, "op-3", "m-tower", "accepted", cites=[ref.event_id],
                                         expected=etag(library), controls=CONTROLS)) == "citation_revoked"


def test_a_retcon_after_planning_refuses_the_commit(world):
    store, handles, library = world
    offered(library, mission(owed_by=(TAM,), owed_to=(WREN,), kind="promise"))
    effects, ref, proposal = _accept_and_bridge(store, handles)
    change = library.record(SCOPE, "op-2", "m-tower", "accepted", cites=[ref.event_id], expected=etag(library),
                            controls=CONTROLS)
    _revoke(effects, handles, ref, proposal)
    before = scope_bytes(store)
    assert reason(change.commit) == "citation_changed"
    assert scope_bytes(store) == before


def test_an_event_that_is_not_there_makes_the_history_uncertain(world):
    store, handles, library = world
    offered(library)
    refuse(library, handles).commit()
    path = next(p for p in store.root.rglob("*.json") if "e-no" in p.name)
    path.unlink()  # as on a device where the event has not synced yet
    state = library.read(SCOPE, requested_by=DIRECTOR).commitment("m-tower")
    assert state.status == "refused" and not state.certain
    assert state.steps[0].support in ("missing", "unreadable")
    assert reason(lambda: library.record(SCOPE, "op-3", "m-tower", "lapsed", cites=["e-ask"],
                                         expected=etag(library))) == "uncertain_history"


def test_a_void_withdraws_an_error_with_its_backers_consent(world):
    store, handles, library = world
    offered(library)
    refuse(library, handles).commit()
    assert reason(lambda: library.void(SCOPE, "op-3", "op-2", reason="wrong scene", expected=etag(library))) \
        == "not_endorsed"
    wrong = endorse_void(handles, "op-2", "another reason")
    assert reason(lambda: library.void(SCOPE, "op-3", "op-2", reason="wrong scene", expected=etag(library),
                                       endorsements=[wrong])) == "invalid_endorsement"
    view = library.void(SCOPE, "op-3", "op-2", reason="wrong scene", expected=etag(library),
                        endorsements=[endorse_void(handles, "op-2", "wrong scene")]).commit()
    assert view.commitment("m-tower").status == "offered"
    assert reason(lambda: library.void(SCOPE, "op-4", "op-2", reason="again", expected=etag(library))) == "invalid_void"
    withdrawn = library.void(SCOPE, "op-5", "op-1", reason="never offered", expected=etag(library)).commit()
    state = withdrawn.commitment("m-tower")
    assert state.status == "voided" and state.deadline.found  # the deadline is left for the director to cancel
    assert reason(lambda: library.record(SCOPE, "op-6", "m-tower", "lapsed", cites=["e-no"],
                                         expected=etag(library))) == "unsupported_commitment"


def test_support_is_the_directors_and_damage_taints_only_what_it_could_hide(world):
    store, handles, library = world
    with pytest.raises(AuthorityError):
        read_support(store, C, ["e-ask"], requested_by="human:ana")
    effects, ref, proposal = _accept_and_bridge(store, handles)
    ghost = Event(C, "e-ghost", "Wren takes a key nobody gave.", Moment(7, "s1", "t7"), accepted_token("e-ghost"))
    EvidenceLibrary(store, handles.app, owner=OWNER).append(C, [ghost]).commit()
    support = read_support(store, C, ["e-ask", ref.event_id, "e-ghost"], requested_by=DIRECTOR).values
    assert support == {"e-ask": "supported", ref.event_id: "supported", "e-ghost": "unreadable"}  # no record to check
    accepted = next(p for p in store.root.rglob("*.json") if p.parent.name == "accepted")
    (accepted.parent / "e-other.json").write_text("{not json")
    damaged = read_support(store, C, ["e-ask", ref.event_id], requested_by=DIRECTOR)
    assert damaged.values == {"e-ask": "supported", ref.event_id: "supported"} and damaged.problems
    accepted.write_text("{not json")
    assert read_support(store, C, [ref.event_id], requested_by=DIRECTOR).values == {ref.event_id: "unreadable"}


def test_inputs_of_the_wrong_shape_are_refusals_and_a_concurrent_write_is_stale(world):
    store, handles, library = world
    offered(library)
    before = scope_bytes(store)
    assert reason(lambda: library.offer(SCOPE, "op-1", mission(), cites=["e-ask"], expected=etag(library),
                                        due=Due("2024-03-02", "New moon", 360))) == "operation_mismatch"
    for bad in (dict(status=["refused"]), dict(status="won"), dict(note=None), dict(cites=None), dict(cites="e-no")):
        fields = dict(status="refused", cites=["e-no"], note="")
        fields.update(bad)
        assert reason(lambda fields=fields: library.record(
            SCOPE, "op-2", "m-tower", fields["status"], cites=fields["cites"], note=fields["note"],
            expected=etag(library), controls=CONTROLS)) == "invalid_record"
    assert reason(lambda: library.void(SCOPE, "op-3", "op-1", reason=None, expected=etag(library))) \
        == "invalid_record"
    assert scope_bytes(store) == before
    tag = etag(library)
    first = library.record(SCOPE, "op-2", "m-tower", "lapsed", cites=["e-no"], expected=tag)
    second = library.record(SCOPE, "op-3", "m-tower", "lapsed", cites=["e-ask"], expected=tag)
    first.commit()
    with pytest.raises(StaleWrite):
        second.commit()


def test_the_record_survives_reopening_and_a_later_advance(tmp_path):
    handles = Handles()
    root = tmp_path / "lib"
    with LibraryStore.create(root) as store:
        library = campaign(store, handles)
        event(store, handles, "e-ask", 1, turn="t1")
        offered(library)
    with LibraryStore.open(root) as store:
        advance(store, handles, "weeks", 20 * 1440)
        state = commitments(store, handles).read(SCOPE, requested_by=DIRECTOR).commitment("m-tower")
        assert state.status == "offered" and state.overdue and state.deadline.triggered_by == "weeks"


def test_two_devices_that_disagree_wait_for_an_explicit_resolution(tmp_path):
    handles = Handles()
    desktop, phone = make_pair(tmp_path)
    path = f"campaigns/{C}/scope.json"
    try:
        left = campaign(desktop, handles)
        event(desktop, handles, "e-ask", 1, turn="t1")
        event(desktop, handles, "e-no", 2, turn="t2")
        sync(desktop, phone, sync_handles())
        right = commitments(phone, handles)
        start = etag(left)
        pending = left.offer(SCOPE, "op-late", mission("m-late", deadline_id=None), cites=["e-ask"], expected=start)
        offered(left)
        right.offer(SCOPE, "op-boat", mission("p-boat", deadline_id=None), cites=["e-ask"], expected=start).commit()
        versions = {DESKTOP: (desktop.root / path).read_bytes(), PHONE: (phone.root / path).read_bytes()}
        assert sync(desktop, phone, sync_handles()).conflict(path).code == "concurrent"
        assert versions[PHONE] in kept(desktop)
        for call in (lambda: left.read(SCOPE, requested_by=DIRECTOR), pending.commit,
                     lambda: right.void(SCOPE, "op-x", "op-boat", reason="dup", expected=start)):
            assert reason(call) == "scope_conflict"
        resolve(desktop, phone, path, etag_of(versions[DESKTOP]),
                {device: etag_of(data) for device, data in versions.items()}, sync_handles())
        for library in (left, right):
            view = library.read(SCOPE, requested_by=DIRECTOR)
            assert [s.commitment_id for s in view.commitments] == ["m-tower"]
    finally:
        desktop.close()
        phone.close()
